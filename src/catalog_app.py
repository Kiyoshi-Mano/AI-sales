import streamlit as st
import pandas as pd
import numpy as np
from pathlib import Path
import logging
from typing import List, Optional, Dict, Any
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import io
import base64

from .pdf_extractor import load_and_extract_pdfs
from .vector_search import VectorSearchEngine, create_dataframe_from_specs
from .models.schemas import MachineSpecs, QAResponse

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CatalogApp:
    """Main application class for machine tool catalog comparison"""

    def __init__(self):
        self.catalog_dir = Path("CatalogDATA")
        self.data_dir = Path("data")
        self.cache_dir = Path("cache")
        self.outputs_dir = Path("outputs")

        # Ensure directories exist
        for directory in [self.data_dir, self.cache_dir, self.outputs_dir]:
            directory.mkdir(exist_ok=True)

        # Initialize components
        self.vector_engine = VectorSearchEngine(self.cache_dir)

        # Initialize session state
        self._init_session_state()

    def _init_session_state(self):
        """Initialize Streamlit session state"""
        if 'specs_df' not in st.session_state:
            st.session_state.specs_df = None
        if 'chat_history' not in st.session_state:
            st.session_state.chat_history = []
        if 'specs_loaded' not in st.session_state:
            st.session_state.specs_loaded = False

    def load_data(self) -> pd.DataFrame:
        """Load and process catalog data"""
        if st.session_state.specs_df is not None and st.session_state.specs_loaded:
            return st.session_state.specs_df

        # Show loading spinner
        with st.spinner("カタログデータを読み込み中..."):
            try:
                # Extract specifications from PDFs
                specs_list = load_and_extract_pdfs(self.catalog_dir)

                if not specs_list:
                    st.error("PDFからデータを抽出できませんでした。")
                    return pd.DataFrame()

                # Convert to DataFrame
                df = create_dataframe_from_specs(specs_list)

                # Build vector index for QA
                self.vector_engine.build_index(self.catalog_dir)

                # Cache in session state
                st.session_state.specs_df = df
                st.session_state.specs_loaded = True

                logger.info(f"Loaded {len(df)} machine specifications")
                return df

            except Exception as e:
                logger.error(f"Error loading data: {e}")
                st.error(f"データ読み込みエラー: {e}")
                return pd.DataFrame()

    def render_sidebar(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Render sidebar with filters"""
        st.sidebar.title("🔧 機械選択・フィルタ")

        if df.empty:
            st.sidebar.warning("データが読み込まれていません")
            return {}

        filters = {}

        # Brand selection
        brands = df['brand'].dropna().unique().tolist()
        selected_brands = st.sidebar.multiselect(
            "メーカー選択",
            options=brands,
            default=brands,
            help="比較するメーカーを選択してください"
        )
        filters['brands'] = selected_brands

        # Filter DataFrame by selected brands
        filtered_df = df[df['brand'].isin(selected_brands)] if selected_brands else df

        # Model selection
        if not filtered_df.empty:
            models = filtered_df['model'].dropna().unique().tolist()
            selected_models = st.sidebar.multiselect(
                "モデル選択",
                options=models,
                default=[],
                help="比較するモデルを選択してください（最大5個推奨）"
            )
            filters['models'] = selected_models

            # Advanced filters
            st.sidebar.subheader("詳細フィルタ")

            # Y-axis range filter
            if 'Y_mm' in filtered_df.columns:
                y_values = filtered_df['Y_mm'].dropna()
                if not y_values.empty:
                    min_y, max_y = int(y_values.min()), int(y_values.max())
                    y_range = st.sidebar.slider(
                        "Y軸ストローク範囲 (mm)",
                        min_value=min_y,
                        max_value=max_y,
                        value=(min_y, max_y)
                    )
                    filters['y_range'] = y_range

            # Spindle RPM filter
            if 'spindle_max_rpm' in filtered_df.columns:
                spindle_values = filtered_df['spindle_max_rpm'].dropna()
                if not spindle_values.empty:
                    min_rpm, max_rpm = int(spindle_values.min()), int(spindle_values.max())
                    rpm_range = st.sidebar.slider(
                        "主軸最大回転数範囲 (rpm)",
                        min_value=min_rpm,
                        max_value=max_rpm,
                        value=(min_rpm, max_rpm)
                    )
                    filters['rpm_range'] = rpm_range

            # Tool count filter
            tool_options = ["14本以上", "28本以上", "100本対応"]
            tool_filter = st.sidebar.selectbox(
                "工具本数",
                options=["制限なし"] + tool_options,
                index=0
            )
            filters['tool_filter'] = tool_filter

            # 5-axis capability
            has_5axis = st.sidebar.checkbox("同時5軸対応のみ")
            filters['has_5axis'] = has_5axis

        else:
            filters['models'] = []

        return filters

    def apply_filters(self, df: pd.DataFrame, filters: Dict[str, Any]) -> pd.DataFrame:
        """Apply filters to DataFrame"""
        filtered_df = df.copy()

        # Brand filter
        if filters.get('brands'):
            filtered_df = filtered_df[filtered_df['brand'].isin(filters['brands'])]

        # Model filter
        if filters.get('models'):
            filtered_df = filtered_df[filtered_df['model'].isin(filters['models'])]

        # Y-axis range filter
        if 'y_range' in filters:
            y_min, y_max = filters['y_range']
            filtered_df = filtered_df[
                (filtered_df['Y_mm'] >= y_min) & (filtered_df['Y_mm'] <= y_max)
            ]

        # RPM range filter
        if 'rpm_range' in filters:
            rpm_min, rpm_max = filters['rpm_range']
            filtered_df = filtered_df[
                (filtered_df['spindle_max_rpm'] >= rpm_min) &
                (filtered_df['spindle_max_rpm'] <= rpm_max)
            ]

        # Tool count filter
        tool_filter = filters.get('tool_filter', '制限なし')
        if tool_filter == "14本以上":
            filtered_df = filtered_df[filtered_df['tool_count'].apply(
                lambda x: isinstance(x, list) and max(x) >= 14 if x else False
            )]
        elif tool_filter == "28本以上":
            filtered_df = filtered_df[filtered_df['tool_count'].apply(
                lambda x: isinstance(x, list) and max(x) >= 28 if x else False
            )]
        elif tool_filter == "100本対応":
            filtered_df = filtered_df[filtered_df['tool_count'].apply(
                lambda x: isinstance(x, list) and max(x) >= 100 if x else False
            )]

        # 5-axis filter
        if filters.get('has_5axis'):
            filtered_df = filtered_df[
                filtered_df['notes'].str.contains('同時5軸', na=False)
            ]

        return filtered_df

    def render_comparison_table(self, df: pd.DataFrame):
        """Render comparison table with highlighting"""
        st.subheader("📊 仕様比較表")

        if df.empty:
            st.warning("比較するモデルを選択してください")
            return

        # Select columns for display
        display_columns = [
            'brand', 'series', 'model',
            'X_mm', 'Y_mm', 'Z_mm', 'Z_alt_mm',
            'spindle_max_rpm', 'rapid_XY_mpm', 'rapid_Z_mpm',
            'table_size_X', 'table_size_Y', 'table_load_kg',
            'tool_count', 'tool_to_tool_s',
            'cnc', 'notes'
        ]

        # Create display DataFrame
        display_df = df[display_columns].copy()

        # Format columns
        numeric_columns = ['X_mm', 'Y_mm', 'Z_mm', 'Z_alt_mm', 'spindle_max_rpm',
                          'rapid_XY_mpm', 'rapid_Z_mpm', 'table_size_X', 'table_size_Y',
                          'table_load_kg', 'tool_to_tool_s']

        for col in numeric_columns:
            display_df[col] = display_df[col].apply(lambda x: f"{x:.0f}" if pd.notnull(x) else "-")

        # Format tool_count (list to string)
        display_df['tool_count'] = display_df['tool_count'].apply(
            lambda x: "/".join(map(str, x)) if isinstance(x, list) else "-"
        )

        # Rename columns for Japanese display
        column_names = {
            'brand': 'メーカー',
            'series': 'シリーズ',
            'model': 'モデル',
            'X_mm': 'X軸(mm)',
            'Y_mm': 'Y軸(mm)',
            'Z_mm': 'Z軸(mm)',
            'Z_alt_mm': 'Z軸OP(mm)',
            'spindle_max_rpm': '主軸最大rpm',
            'rapid_XY_mpm': '早送りXY',
            'rapid_Z_mpm': '早送りZ',
            'table_size_X': 'テーブルX',
            'table_size_Y': 'テーブルY',
            'table_load_kg': '許容質量(kg)',
            'tool_count': '工具本数',
            'tool_to_tool_s': 'T-T時間(s)',
            'cnc': 'CNC',
            'notes': '特記事項'
        }

        display_df = display_df.rename(columns=column_names)

        # Style the dataframe with highlighting
        def highlight_max_min(s):
            if s.name in ['主軸最大rpm', 'Y軸(mm)', 'Z軸(mm)', '許容質量(kg)', '早送りXY', '早送りZ']:
                try:
                    numeric_s = pd.to_numeric(s.replace('-', np.nan), errors='coerce')
                    if numeric_s.notna().sum() > 1:
                        styles = [''] * len(s)
                        max_idx = numeric_s.idxmax()
                        min_idx = numeric_s.idxmin()
                        styles[max_idx] = 'background-color: #90EE90'  # Light green for max
                        styles[min_idx] = 'background-color: #FFB6C1'  # Light pink for min
                        return styles
                except:
                    pass
            return [''] * len(s)

        # Display styled table
        styled_df = display_df.style.apply(highlight_max_min, axis=0)
        st.dataframe(styled_df, use_container_width=True)

        # Export functionality
        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("CSV出力"):
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_path = self.outputs_dir / f"compare_{timestamp}.csv"
                display_df.to_csv(output_path, index=False, encoding='utf-8-sig')
                st.success(f"比較結果を {output_path} に出力しました")

    def render_kpi_cards(self, df: pd.DataFrame):
        """Render KPI cards for selected models"""
        if df.empty or len(df) > 3:
            return

        st.subheader("📈 主要KPI")

        cols = st.columns(len(df))

        for i, (_, row) in enumerate(df.iterrows()):
            with cols[i]:
                st.metric(
                    label=f"{row['brand']} {row['model']}",
                    value=f"Y軸: {row['Y_mm']:.0f}mm" if pd.notnull(row['Y_mm']) else "Y軸: -",
                    delta=f"工具: {'/'.join(map(str, row['tool_count']))}" if isinstance(row['tool_count'], list) else "工具: -"
                )

                if pd.notnull(row['rapid_XY_mpm']):
                    st.metric("早送りXY", f"{row['rapid_XY_mpm']:.0f} m/min")

                if pd.notnull(row['table_load_kg']):
                    st.metric("許容質量", f"{row['table_load_kg']:.0f} kg")

    def render_qa_chat(self):
        """Render QA chat interface"""
        st.subheader("💬 カタログQA")
        st.write("カタログの内容について質問してください。回答には必ず根拠となるページ番号が示されます。")

        # Brand filter for QA
        col1, col2 = st.columns([2, 3])
        with col1:
            qa_brand = st.selectbox(
                "メーカー絞り込み",
                options=["全て", "BROTHER", "FANUC", "OKUMA"],
                index=0,
                help="特定のメーカーのカタログのみで回答する場合は選択"
            )

        # Chat input
        user_question = st.text_input(
            "質問を入力してください",
            placeholder="例: Brother Xd2シリーズのY軸ストロークは何mmですか？",
            key="qa_input"
        )

        if st.button("質問する", type="primary"):
            if user_question.strip():
                with st.spinner("回答を生成中..."):
                    try:
                        # Get answer using RAG
                        brand_filter = qa_brand if qa_brand != "全て" else None
                        response = self.vector_engine.answer_question(
                            user_question,
                            brand_filter=brand_filter,
                            top_k=5
                        )

                        # Add to chat history
                        st.session_state.chat_history.append({
                            'question': user_question,
                            'answer': response.answer,
                            'citations': response.citations,
                            'confidence': response.confidence,
                            'timestamp': datetime.now()
                        })

                    except Exception as e:
                        st.error(f"回答生成エラー: {e}")

        # Display chat history
        if st.session_state.chat_history:
            st.subheader("📝 質問履歴")

            for i, chat in enumerate(reversed(st.session_state.chat_history[-5:])):
                with st.container():
                    st.write(f"**Q{len(st.session_state.chat_history)-i}**: {chat['question']}")
                    st.write(f"**A**: {chat['answer']}")

                    # Show citations
                    if chat['citations']:
                        st.write("**根拠資料:**")
                        for citation in chat['citations']:
                            confidence_color = "🟢" if citation.score > 0.8 else "🟡" if citation.score > 0.6 else "🔴"
                            st.write(f"{confidence_color} {citation.source} p.{citation.page} (関連度: {citation.score:.2f})")

                    st.write(f"*信頼度: {chat['confidence']:.2f} | {chat['timestamp'].strftime('%H:%M:%S')}*")
                    st.divider()

        # Clear history button
        if st.session_state.chat_history:
            if st.button("履歴をクリア"):
                st.session_state.chat_history = []
                st.experimental_rerun()

    def render_features_summary(self, df: pd.DataFrame):
        """Render features summary for selected models"""
        st.subheader("🔍 選択モデルの特徴要約")

        if df.empty:
            st.warning("モデルを選択してください")
            return

        for _, row in df.iterrows():
            with st.expander(f"{row['brand']} {row['model']} の特徴"):
                col1, col2 = st.columns(2)

                with col1:
                    st.write("**基本仕様:**")
                    if pd.notnull(row['X_mm']):
                        st.write(f"• X軸: {row['X_mm']:.0f}mm")
                    if pd.notnull(row['Y_mm']):
                        st.write(f"• Y軸: {row['Y_mm']:.0f}mm")
                    if pd.notnull(row['Z_mm']):
                        st.write(f"• Z軸: {row['Z_mm']:.0f}mm")
                        if pd.notnull(row['Z_alt_mm']):
                            st.write(f"  (オプション: {row['Z_alt_mm']:.0f}mm)")

                with col2:
                    st.write("**性能:**")
                    if pd.notnull(row['spindle_max_rpm']):
                        st.write(f"• 主軸最大: {row['spindle_max_rpm']:.0f}rpm")
                    if pd.notnull(row['rapid_XY_mpm']):
                        st.write(f"• 早送りXY: {row['rapid_XY_mpm']:.0f}m/min")
                    if pd.notnull(row['rapid_Z_mpm']):
                        st.write(f"• 早送りZ: {row['rapid_Z_mpm']:.0f}m/min")

                if pd.notnull(row['notes']) and row['notes']:
                    st.write("**特記事項:**")
                    notes = row['notes'].split(';')
                    for note in notes:
                        if note.strip():
                            st.write(f"• {note.strip()}")

    def run(self):
        """Run the main application"""
        st.set_page_config(
            page_title="工作機械セールス支援アプリ",
            page_icon="🔧",
            layout="wide",
            initial_sidebar_state="expanded"
        )

        st.title("🔧 工作機械セールス支援アプリ")
        st.markdown("Brother, FANUC, OKUMA の機械仕様を横並び比較・カタログQA")

        # Load data
        df = self.load_data()

        if not df.empty:
            # Render sidebar and get filters
            filters = self.render_sidebar(df)

            # Apply filters
            filtered_df = self.apply_filters(df, filters)

            # Main content tabs
            tab1, tab2, tab3, tab4 = st.tabs(["📊 比較", "🔍 特徴", "💬 カタログQA", "📁 PDFビューワ"])

            with tab1:
                if not filtered_df.empty:
                    # Show KPI cards for selected models
                    if filters.get('models'):
                        selected_df = filtered_df[filtered_df['model'].isin(filters['models'])]
                        self.render_kpi_cards(selected_df)

                    # Show comparison table
                    self.render_comparison_table(filtered_df)
                else:
                    st.warning("選択した条件に該当するデータがありません")

            with tab2:
                if filters.get('models'):
                    selected_df = filtered_df[filtered_df['model'].isin(filters['models'])]
                    self.render_features_summary(selected_df)
                else:
                    st.info("サイドバーでモデルを選択してください")

            with tab3:
                self.render_qa_chat()

            with tab4:
                st.subheader("📁 PDFカタログビューワ")
                st.info("PDF閲覧機能は今後実装予定です")

                # List available PDFs
                pdf_files = list(self.catalog_dir.glob("*.pdf"))
                if pdf_files:
                    st.write("利用可能なカタログ:")
                    for pdf in pdf_files:
                        st.write(f"• {pdf.name}")

        else:
            st.error("データの読み込みに失敗しました。CatalogDATAディレクトリにPDFファイルがあることを確認してください。")


def main():
    """Main entry point"""
    app = CatalogApp()
    app.run()


if __name__ == "__main__":
    main()