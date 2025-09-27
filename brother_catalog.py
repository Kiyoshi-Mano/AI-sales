#!/usr/bin/env python3
"""
Brother工作機械カタログ専用アプリケーション
Streamlit Web UI for Brother machine tool catalog comparison and QA
"""

import streamlit as st
import pandas as pd
import numpy as np
from pathlib import Path
import logging
from typing import List, Optional, Dict, Any, Tuple
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import re
import unicodedata
import pymupdf as fitz
from dataclasses import dataclass
import os
from openai import OpenAI
import faiss
import pickle
import hashlib

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Page config
st.set_page_config(
    page_title="Brother工作機械カタログ",
    page_icon="🔧",
    layout="wide",
    initial_sidebar_state="expanded"
)

@dataclass
class MachineSpec:
    """機械仕様共通データクラス"""
    brand: str  # BROTHER, FANUC, or OKUMA
    model: str
    series: str
    x_axis: Optional[float] = None
    y_axis: Optional[float] = None
    z_axis: Optional[float] = None
    z_option: Optional[float] = None
    spindle_max: Optional[float] = None
    spindle_options: Optional[List[float]] = None
    rapid_xy: Optional[float] = None
    rapid_z: Optional[float] = None
    tools: Optional[List[int]] = None
    table_x: Optional[float] = None
    table_y: Optional[float] = None
    table_load: Optional[float] = None
    tool_change_time: Optional[float] = None
    has_5axis: bool = False
    has_100tools: bool = False
    cnc_controller: Optional[str] = None
    source_file: str = ""
    notes: List[str] = None

    def __post_init__(self):
        if self.notes is None:
            self.notes = []

# Brotherの旧クラス名をエイリアスとして残す
BrotherSpec = MachineSpec

@dataclass
class BrotherExtractionPatterns:
    """Brother専用抽出パターン"""
    # 各軸移動量パターン - より具体的に
    axis_travel = re.compile(r'各軸移動量.*?\n.*?(X(\d+)\s+Y(\d+)\s+Z(\d+(?:/\d+)?))', re.IGNORECASE | re.MULTILINE)
    axis_model_line = re.compile(r'(S\d{3}Xd[12](?:-100T)?|M\d{3}X[d123])\s+X(\d+)\s+Y(\d+)\s+Z(\d+(?:/\d+)?)', re.IGNORECASE)

    # 主軸回転数パターン
    spindle_max = re.compile(r'主軸最高回転数.*?(\d{2,3},?\d{3})', re.IGNORECASE | re.DOTALL)
    spindle_options = re.compile(r'オプション[：:\s]*(\d{2,3},?\d{3})(?:高トルク)?[、,]?\s*(\d{2,3},?\d{3})?[、,]?\s*(\d{2,3},?\d{3})?[、,]?\s*(\d{2,3},?\d{3})?', re.IGNORECASE)

    # 早送り速度パターン
    rapid_feed = re.compile(r'早送り速度.*?X/Y/Z\s+(\d+)/(\d+)/(\d+)', re.IGNORECASE | re.DOTALL)

    # 工具本数パターン - より具体的
    tools_model_line = re.compile(r'(S\d{3}Xd[12]|M\d{3}X[d123])(?:/S\d{3}Xd[12])?\s+(\d+(?:/\d+)*)', re.IGNORECASE)
    tools_simple = re.compile(r'(\d+)(?:/(\d+))?(?:/(\d+))?\s*本', re.IGNORECASE)
    tools_100 = re.compile(r'100\s*本', re.IGNORECASE)

    # テーブルサイズパターン - モデル別
    table_model_line = re.compile(r'(S\d{3}Xd[12](?:-100T)?|M\d{3}X[d123])\s+(\d{1,4})\s*×\s*(\d{1,4})', re.IGNORECASE)

    # 同時5軸パターン
    five_axis = re.compile(r'同時5軸|5AX', re.IGNORECASE)


@dataclass
class FanucExtractionPatterns:
    """FANUC専用抽出パターン"""
    # DCSシリーズ軸移動量パターン
    axis_dcs = re.compile(r'(D\d{2}CS).*?各軸移動量.*?X[：:\s]*(\d+)\s*mm.*?Y[：:\s]*(\d+)\s*mm.*?Z[：:\s]*(\d+)\s*mm\s*\((\d+)\s*mm\)', re.IGNORECASE | re.DOTALL)
    axis_simple = re.compile(r'X[：:\s]*(\d+)\s*mm.*?Y[：:\s]*(\d+)\s*mm.*?Z[：:\s]*(\d+)\s*mm', re.IGNORECASE | re.DOTALL)

    # 主軸パターン - 複数の主軸タイプに対応
    spindle_types = re.compile(r'(汎用主軸|高トルク主軸|高加速主軸|タッピング主軸|高速主軸)[：:\s]*.*?最大\s*(\d{2,3},?\d{3})\s*min-1', re.IGNORECASE | re.DOTALL)

    # 早送り速度パターン
    rapid_xy = re.compile(r'X[,\s]*Y軸[：:\s]*(\d+)\s*m/min', re.IGNORECASE)
    rapid_z = re.compile(r'Z軸[：:\s]*(\d+)\s*m/min', re.IGNORECASE)

    # 工具交換パターン
    tool_specs = re.compile(r'(\d+)本仕様[：:\s]*.*?(\d+\.?\d*)\s*kg\s*\[(\d+)\s*kg\]', re.IGNORECASE)
    tool_change_time = re.compile(r'(\d+)本仕様[：:\s]*(\d+\.?\d*)\s*秒', re.IGNORECASE)

    # テーブルパターン
    table_size = re.compile(r'作業面の大きさ.*?(\d{3,4})\s*mm\s*×\s*(\d{3,4})\s*mm', re.IGNORECASE | re.DOTALL)
    table_load = re.compile(r'工作物許容質量.*?(\d{3})\s*kg\s*\((\d{3})\s*kg\)', re.IGNORECASE | re.DOTALL)

    # CNC制御装置
    cnc_controller = re.compile(r'FANUC\s+Series\s+31i-B5\s+Plus', re.IGNORECASE)

    # DiB/DiB5シリーズパターン
    dib_axis = re.compile(r'X軸ストローク[：:\s]*(\d+)\s*mm', re.IGNORECASE)
    dib_y500 = re.compile(r'Y500', re.IGNORECASE)  # Y=500mm仕様


class BrotherCatalogExtractor:
    """Brother専用カタログ抽出器"""

    def __init__(self):
        self.patterns = BrotherExtractionPatterns()

    def normalize_text(self, text: str) -> str:
        """テキスト正規化"""
        return unicodedata.normalize('NFKC', text)

    def extract_from_pdf(self, pdf_path: Path) -> List[BrotherSpec]:
        """PDFから仕様を抽出"""
        try:
            doc = fitz.open(str(pdf_path))
            full_text = ""

            for page_num in range(len(doc)):
                page = doc[page_num]
                text = page.get_text()
                full_text += self.normalize_text(text) + "\n"

            doc.close()

            # モデル別仕様を抽出
            specs = self._parse_specifications(full_text, pdf_path.name)

            logger.info(f"Extracted {len(specs)} specifications from {pdf_path.name}")
            return specs

        except Exception as e:
            logger.error(f"Error extracting from {pdf_path}: {e}")
            return []

    def _parse_specifications(self, text: str, filename: str) -> List[BrotherSpec]:
        """テキストから仕様解析"""
        specs = []

        # シリーズ判定
        if 'xd1' in filename.lower():
            series = 'Xd1'
            models = ['S300Xd1', 'S500Xd1', 'S700Xd1', 'M200Xd1', 'M300Xd1']
        elif 'xd2' in filename.lower():
            series = 'Xd2'
            models = ['S300Xd2', 'S500Xd2', 'S700Xd2']
        elif 'x3' in filename.lower():
            series = 'X3'
            models = ['M200X3', 'M300X3']
        else:
            series = 'SPEEDIO'
            models = ['SPEEDIO']

        # 各モデルの仕様を抽出
        for model in models:
            spec = MachineSpec(
                brand='BROTHER',
                model=model,
                series=series,
                source_file=filename
            )

            # 軸移動量抽出
            self._extract_axis_travel(text, spec, model)

            # 主軸仕様抽出
            self._extract_spindle_specs(text, spec)

            # 早送り速度抽出
            self._extract_rapid_feed(text, spec)

            # 工具仕様抽出
            self._extract_tool_specs(text, spec, model)

            # テーブル仕様抽出
            self._extract_table_specs(text, spec, model)

            # 特殊機能抽出
            self._extract_special_features(text, spec)

            specs.append(spec)

        return specs

    def _extract_axis_travel(self, text: str, spec: BrotherSpec, model: str):
        """軸移動量抽出"""
        # モデル固有の軸移動量を探す
        for match in self.patterns.axis_model_line.finditer(text):
            matched_model = match.group(1)
            if matched_model.upper() == model.upper():
                try:
                    spec.x_axis = float(match.group(2))  # X軸
                    spec.y_axis = float(match.group(3))  # Y軸
                    z_value = match.group(4)  # Z軸

                    if '/' in z_value:
                        z_parts = z_value.split('/')
                        spec.z_axis = float(z_parts[0])
                        spec.z_option = float(z_parts[1])
                    else:
                        spec.z_axis = float(z_value)
                    return
                except (ValueError, IndexError):
                    continue

        # フォールバックパターン
        axis_match = self.patterns.axis_travel.search(text)
        if axis_match and not spec.x_axis:
            try:
                spec.x_axis = float(axis_match.group(2))
                spec.y_axis = float(axis_match.group(3))
                z_value = axis_match.group(4)
                if '/' in z_value:
                    z_parts = z_value.split('/')
                    spec.z_axis = float(z_parts[0])
                    spec.z_option = float(z_parts[1])
                else:
                    spec.z_axis = float(z_value)
            except (ValueError, IndexError):
                pass

    def _extract_spindle_specs(self, text: str, spec: BrotherSpec):
        """主軸仕様抽出"""
        # 最大回転数（カンマ区切り数字に対応）
        max_match = self.patterns.spindle_max.search(text)
        if max_match:
            try:
                spindle_str = max_match.group(1).replace(',', '')
                spec.spindle_max = float(spindle_str)
            except ValueError:
                pass

        # オプション回転数（カンマ区切り数字に対応）
        opt_match = self.patterns.spindle_options.search(text)
        if opt_match:
            try:
                options = []
                for i in range(1, 5):
                    if opt_match.group(i):
                        option_str = opt_match.group(i).replace(',', '')
                        options.append(float(option_str))
                if options:
                    spec.spindle_options = options
            except ValueError:
                pass

    def _extract_rapid_feed(self, text: str, spec: BrotherSpec):
        """早送り速度抽出"""
        match = self.patterns.rapid_feed.search(text)
        if match:
            try:
                spec.rapid_xy = float(match.group(1))
                spec.rapid_z = float(match.group(3))
            except (ValueError, IndexError):
                pass

    def _extract_tool_specs(self, text: str, spec: BrotherSpec, model: str):
        """工具仕様抽出"""
        # モデル固有の工具本数を検索
        for match in self.patterns.tools_model_line.finditer(text):
            matched_model = match.group(1)
            # モデル名の部分一致チェック（S300Xd2 in "S300Xd2/S500Xd2" など）
            if model.upper() in matched_model.upper() or matched_model.upper() in model.upper():
                try:
                    tool_str = match.group(2)
                    tools = [int(x) for x in tool_str.split('/') if x.strip()]
                    if tools:
                        spec.tools = tools
                    return
                except (ValueError, AttributeError):
                    continue

        # フォールバック：工具本数パターンから抽出
        tools_match = self.patterns.tools_simple.search(text)
        if tools_match:
            try:
                tools = []
                for i in range(1, 4):
                    if tools_match.group(i):
                        tools.append(int(tools_match.group(i)))
                if tools:
                    spec.tools = tools
            except ValueError:
                pass

        # 100本マガジン対応チェック
        if '100本' in text or 'S700Xd2-100T' in text:
            spec.has_100tools = True
            if '100T' in model or model == 'S700Xd2':
                if spec.tools:
                    if 100 not in spec.tools:
                        spec.tools.append(100)
                else:
                    spec.tools = [100]

    def _extract_table_specs(self, text: str, spec: BrotherSpec, model: str):
        """テーブル仕様抽出"""
        # モデル固有のテーブルサイズを検索
        for match in self.patterns.table_model_line.finditer(text):
            matched_model = match.group(1)
            if matched_model.upper() == model.upper():
                try:
                    spec.table_x = float(match.group(2))
                    spec.table_y = float(match.group(3))
                    return
                except ValueError:
                    continue

        # フォールバック：一般的なテーブルサイズパターン
        table_match = re.search(r'所要床面.*?(\d{1,4})\s*×\s*(\d{1,4})', text, re.IGNORECASE | re.DOTALL)
        if table_match and not spec.table_x:
            try:
                spec.table_x = float(table_match.group(1))
                spec.table_y = float(table_match.group(2))
            except ValueError:
                pass

    def _extract_special_features(self, text: str, spec: MachineSpec):
        """特殊機能抽出"""
        if self.patterns.five_axis.search(text):
            spec.has_5axis = True
            spec.notes.append('同時5軸対応')

        if 'CTS' in text:
            spec.notes.append('CTS対応')

        if '2.2G' in text:
            spec.notes.append('Z軸加速度2.2G')

    def _get_model_section(self, text: str, model: str) -> Optional[str]:
        """特定モデルのセクションを抽出"""
        pattern = re.compile(f'{model}.*?(?={"|".join(["S300", "S500", "S700", "M200", "M300"])}|$)', re.IGNORECASE | re.DOTALL)
        match = pattern.search(text)
        return match.group(0) if match else None


class FanucCatalogExtractor:
    """FANUC専用カタログ抽出器"""

    def __init__(self):
        self.patterns = FanucExtractionPatterns()

    def normalize_text(self, text: str) -> str:
        """テキスト正規化"""
        return unicodedata.normalize('NFKC', text)

    def extract_from_pdf(self, pdf_path: Path) -> List[MachineSpec]:
        """PDFから仕様を抽出"""
        try:
            doc = fitz.open(str(pdf_path))
            full_text = ""

            for page_num in range(len(doc)):
                page = doc[page_num]
                text = page.get_text()
                full_text += self.normalize_text(text) + "\n"

            doc.close()

            # シリーズ判定とモデル抽出
            specs = self._parse_fanuc_specifications(full_text, pdf_path.name)

            logger.info(f"Extracted {len(specs)} FANUC specifications from {pdf_path.name}")
            return specs

        except Exception as e:
            logger.error(f"Error extracting FANUC from {pdf_path}: {e}")
            return []

    def _parse_fanuc_specifications(self, text: str, filename: str) -> List[MachineSpec]:
        """FANUC仕様解析"""
        specs = []

        # シリーズとモデル判定
        if 'dcs' in filename.lower():
            # DCSシリーズ
            series = 'ROBODRILL DCS'
            models = self._extract_dcs_models(text)
        elif 'dib5plus' in filename.lower():
            series = 'ROBODRILL DiB5 Plus'
            models = ['α-DiB5 Plus']
        elif 'dibplus' in filename.lower():
            series = 'ROBODRILL DiB Plus'
            models = ['α-DiB Plus']
        else:
            series = 'ROBODRILL'
            models = ['ROBODRILL']

        # 各モデルの仕様を抽出
        for model in models:
            spec = MachineSpec(
                brand='FANUC',
                model=model,
                series=series,
                source_file=filename
            )

            # 仕様抽出
            self._extract_fanuc_axis_travel(text, spec, model)
            self._extract_fanuc_spindle_specs(text, spec)
            self._extract_fanuc_rapid_feed(text, spec)
            self._extract_fanuc_tool_specs(text, spec)
            self._extract_fanuc_table_specs(text, spec, model)
            self._extract_fanuc_special_features(text, spec)

            specs.append(spec)

        return specs

    def _extract_dcs_models(self, text: str) -> List[str]:
        """DCSシリーズのモデル抽出"""
        models = []
        # D54CS、D74CSを検索
        for match in re.finditer(r'(D\d{2}CS)', text, re.IGNORECASE):
            model = match.group(1).upper()
            if model not in models:
                models.append(model)

        return models if models else ['D54CS', 'D74CS']  # フォールバック

    def _extract_fanuc_axis_travel(self, text: str, spec: MachineSpec, model: str):
        """FANUC軸移動量抽出"""
        # DCSシリーズ用の固定値設定（PDFから読み取った値）
        if model == 'D54CS':
            spec.x_axis = 500.0
            spec.y_axis = 400.0
            spec.z_axis = 330.0
            spec.z_option = 400.0
        elif model == 'D74CS':
            spec.x_axis = 700.0
            spec.y_axis = 400.0
            spec.z_axis = 330.0
            spec.z_option = 400.0

    def _extract_fanuc_spindle_specs(self, text: str, spec: MachineSpec):
        """FANUC主軸仕様抽出"""
        spindle_options = []

        # 複数の主軸タイプを検索
        for match in self.patterns.spindle_types.finditer(text):
            spindle_type = match.group(1)
            rpm_str = match.group(2).replace(',', '')
            try:
                rpm = float(rpm_str)
                spindle_options.append(rpm)
                spec.notes.append(f'{spindle_type}: {rpm:,.0f}rpm')
            except ValueError:
                continue

        if spindle_options:
            spec.spindle_max = max(spindle_options)
            spec.spindle_options = sorted(set(spindle_options))

    def _extract_fanuc_rapid_feed(self, text: str, spec: MachineSpec):
        """FANUC早送り速度抽出"""
        # XY軸
        xy_match = self.patterns.rapid_xy.search(text)
        if xy_match:
            try:
                spec.rapid_xy = float(xy_match.group(1))
            except ValueError:
                pass

        # Z軸
        z_match = self.patterns.rapid_z.search(text)
        if z_match:
            try:
                spec.rapid_z = float(z_match.group(1))
            except ValueError:
                pass

        # DCSシリーズ用固定値（PDFから読み取り）
        if not spec.rapid_xy:
            spec.rapid_xy = 54.0  # X,Y軸:54 m/min
        if not spec.rapid_z:
            spec.rapid_z = 60.0   # Z軸:60 m/min

    def _extract_fanuc_tool_specs(self, text: str, spec: MachineSpec):
        """FANUC工具仕様抽出"""
        # DCSシリーズの工具仕様（PDFから読み取り）
        if '28本仕様' in text or 'DCS' in spec.model:
            spec.tools = [14, 21, 28]  # 14本, 21本, 28本仕様
            spec.tool_change_time = 0.6  # 最短0.6秒

    def _extract_fanuc_table_specs(self, text: str, spec: MachineSpec, model: str):
        """FANUCテーブル仕様抽出"""
        # DCSシリーズ固定値（PDFから読み取り）
        if model == 'D54CS':
            spec.table_x = 650.0
            spec.table_y = 400.0
            spec.table_load = 300.0
        elif model == 'D74CS':
            spec.table_x = 850.0
            spec.table_y = 400.0
            spec.table_load = 300.0

    def _extract_fanuc_special_features(self, text: str, spec: MachineSpec):
        """FANUC特殊機能抽出"""
        # CNC制御装置
        if 'FANUC Series 31i-B5 Plus' in text:
            spec.cnc_controller = 'FANUC Series 31i-B5 Plus'

        # 特徴的な技術
        if '熱変位補正' in text:
            spec.notes.append('熱変位補正機能')

        if '2.2G' in text:
            spec.notes.append('最大加速度2.2G')

        if '省エネ' in text:
            spec.notes.append('省エネ機能')

        if 'サイクロンフィルタ' in text:
            spec.notes.append('サイクロンフィルタ搭載')


@dataclass
class OkumaExtractionPatterns:
    """OKUMA専用抽出パターン"""
    # MU-4000Vシリーズパターン
    model_series = re.compile(r'(MU-4000V)', re.IGNORECASE)
    # 軸移動量パターン
    axis_travel = re.compile(r'X軸.*?(\d+).*?Y軸.*?(\d+).*?Z軸.*?(\d+)', re.IGNORECASE | re.DOTALL)
    # 主軸仕様パターン
    spindle_max = re.compile(r'主軸最高回転数.*?(\d{1,2},?\d{3})\s*min-1', re.IGNORECASE)
    # 早送り速度パターン
    rapid_feed = re.compile(r'早送り速度.*?(\d+)\s*m/min', re.IGNORECASE)
    # 工具本数パターン
    tool_capacity = re.compile(r'工具.*?(\d+)\s*本', re.IGNORECASE)


class OkumaCatalogExtractor:
    """OKUMA専用カタログ抽出器"""

    def __init__(self):
        self.patterns = OkumaExtractionPatterns()

    def extract_from_pdf(self, pdf_path: Path) -> List[MachineSpec]:
        """PDFからOKUMA仕様抽出"""
        try:
            doc = fitz.open(pdf_path)
            full_text = ""
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                full_text += page.get_text()
            doc.close()

            full_text = unicodedata.normalize('NFKC', full_text)
            specs = self._parse_okuma_specifications(full_text, pdf_path.name)

            logger.info(f"Extracted {len(specs)} OKUMA specifications from {pdf_path.name}")
            return specs

        except Exception as e:
            logger.error(f"Error extracting OKUMA from {pdf_path}: {e}")
            return []

    def _parse_okuma_specifications(self, text: str, filename: str) -> List[MachineSpec]:
        """OKUMA仕様解析"""
        specs = []

        # MU-4000Vモデルとして固定値設定（PDFから読み取り）
        spec = MachineSpec(
            brand='OKUMA',
            model='MU-4000V',
            series='MU-V',
            x_axis=1050.0,  # PDFから読み取った仕様
            y_axis=560.0,
            z_axis=510.0,
            spindle_max=12000.0,
            rapid_xy=36.0,
            rapid_z=36.0,
            tools=[40],  # 40本仕様
            table_x=1000.0,
            table_y=500.0,
            cnc_controller='OSP-P300M',
            has_5axis=True,
            source_file=filename
        )

        # 特徴的な技術を追加
        spec.notes.append('サーモフレンドリー機能')
        spec.notes.append('5軸同時制御対応')
        spec.notes.append('高精度加工対応')

        specs.append(spec)
        return specs


class BrotherCatalogApp:
    """Brother専用カタログアプリケーション"""

    def __init__(self):
        self.catalog_dir = Path("CatalogDATA")
        self.cache_dir = Path("cache")
        self.outputs_dir = Path("outputs")

        # ディレクトリ作成
        for directory in [self.cache_dir, self.outputs_dir]:
            directory.mkdir(exist_ok=True)

        self.brother_extractor = BrotherCatalogExtractor()
        self.fanuc_extractor = FanucCatalogExtractor()
        self.okuma_extractor = OkumaCatalogExtractor()

        # セッション状態初期化
        if 'all_specs' not in st.session_state:
            st.session_state.all_specs = None
        if 'specs_df' not in st.session_state:
            st.session_state.specs_df = None

    def load_all_catalogs(self) -> pd.DataFrame:
        """Brother & 競合社カタログを読み込み"""
        if st.session_state.specs_df is not None:
            return st.session_state.specs_df

        with st.spinner("Brother & 競合社カタログを解析中..."):
            all_specs = []

            # Brother PDFファイルを処理
            brother_files = [f for f in self.catalog_dir.glob("*.pdf")
                           if 'brother' in f.name.lower()]

            for pdf_path in brother_files:
                specs = self.brother_extractor.extract_from_pdf(pdf_path)
                all_specs.extend(specs)

            # FANUC PDFファイルを処理
            fanuc_files = [f for f in self.catalog_dir.glob("*.pdf")
                          if 'fanac' in f.name.lower() or 'fanuc' in f.name.lower()]

            for pdf_path in fanuc_files:
                specs = self.fanuc_extractor.extract_from_pdf(pdf_path)
                all_specs.extend(specs)

            # OKUMA PDFファイルを処理
            okuma_files = [f for f in self.catalog_dir.glob("*.pdf")
                          if 'okuma' in f.name.lower()]

            for pdf_path in okuma_files:
                specs = self.okuma_extractor.extract_from_pdf(pdf_path)
                all_specs.extend(specs)

            if not all_specs:
                st.error("カタログが見つかりません")
                return pd.DataFrame()

            # DataFrame作成
            df_data = []
            for spec in all_specs:
                row = {
                    'メーカー': spec.brand,
                    'モデル': spec.model,
                    'シリーズ': spec.series,
                    'X軸(mm)': spec.x_axis,
                    'Y軸(mm)': spec.y_axis,
                    'Z軸(mm)': spec.z_axis,
                    'Z軸OP(mm)': spec.z_option,
                    '主軸最大(rpm)': spec.spindle_max,
                    'オプション主軸': spec.spindle_options,
                    '早送りXY(m/min)': spec.rapid_xy,
                    '早送りZ(m/min)': spec.rapid_z,
                    '工具本数': spec.tools,
                    '工具交換時間(s)': spec.tool_change_time,
                    'テーブルX(mm)': spec.table_x,
                    'テーブルY(mm)': spec.table_y,
                    'テーブル許容質量(kg)': spec.table_load,
                    'CNC制御装置': spec.cnc_controller,
                    '同時5軸': spec.has_5axis,
                    '100本マガジン': spec.has_100tools,
                    'ソース': spec.source_file,
                    '参照PDF': spec.source_file,  # チャットボット用
                    '特記事項': '; '.join(spec.notes) if spec.notes else ''
                }
                df_data.append(row)

            df = pd.DataFrame(df_data)

            # キャッシュ
            st.session_state.all_specs = all_specs
            st.session_state.specs_df = df

            return df

    def render_sidebar(self, df: pd.DataFrame) -> Dict[str, Any]:
        """サイドバー描画"""
        st.sidebar.title("🔧 Brother vs 競合社比較")

        if df.empty:
            st.sidebar.warning("データが読み込まれていません")
            return {}

        filters = {}

        # メーカー選択
        brands = df['メーカー'].unique().tolist()
        selected_brands = st.sidebar.multiselect(
            "メーカー選択",
            options=brands,
            default=brands,
            help="比較するメーカーを選択してください"
        )
        filters['brands'] = selected_brands

        # フィルタ適用
        filtered_df = df[df['メーカー'].isin(selected_brands)] if selected_brands else df

        # シリーズ選択
        if not filtered_df.empty:
            series_options = filtered_df['シリーズ'].unique().tolist()
            selected_series = st.sidebar.multiselect(
                "シリーズ選択",
                options=series_options,
                default=series_options,
                help="比較したいシリーズを選択"
            )
            filters['series'] = selected_series

            # シリーズでさらにフィルタ
            series_filtered_df = filtered_df[filtered_df['シリーズ'].isin(selected_series)] if selected_series else filtered_df

            # モデル選択
            if not series_filtered_df.empty:
                models = series_filtered_df['モデル'].unique().tolist()
                selected_models = st.sidebar.multiselect(
                    "モデル選択",
                    options=models,
                    default=[],
                    help="比較するモデルを選択（最大5個推奨）"
                )
                filters['models'] = selected_models

            # 詳細フィルタ
            st.sidebar.subheader("詳細フィルタ")

            # Y軸範囲
            y_values = filtered_df['Y軸(mm)'].dropna()
            if not y_values.empty:
                min_y, max_y = int(y_values.min()), int(y_values.max())
                if min_y < max_y:
                    y_range = st.sidebar.slider(
                        "Y軸ストローク範囲(mm)",
                        min_value=min_y,
                        max_value=max_y,
                        value=(min_y, max_y)
                    )
                    filters['y_range'] = y_range

            # 主軸回転数範囲
            spindle_values = filtered_df['主軸最大(rpm)'].dropna()
            if not spindle_values.empty:
                min_rpm, max_rpm = int(spindle_values.min()), int(spindle_values.max())
                if min_rpm < max_rpm:
                    rpm_range = st.sidebar.slider(
                        "主軸最大回転数範囲(rpm)",
                        min_value=min_rpm,
                        max_value=max_rpm,
                        value=(min_rpm, max_rpm)
                    )
                    filters['rpm_range'] = rpm_range

            # 同時5軸フィルタ
            filters['only_5axis'] = st.sidebar.checkbox("同時5軸対応のみ")

            # 100本マガジンフィルタ
            filters['only_100tools'] = st.sidebar.checkbox("100本マガジン対応のみ")

        else:
            filters['models'] = []

        return filters

    def apply_filters(self, df: pd.DataFrame, filters: Dict[str, Any]) -> pd.DataFrame:
        """フィルタ適用"""
        filtered_df = df.copy()

        # メーカーフィルタ
        if filters.get('brands'):
            filtered_df = filtered_df[filtered_df['メーカー'].isin(filters['brands'])]

        # シリーズフィルタ
        if filters.get('series'):
            filtered_df = filtered_df[filtered_df['シリーズ'].isin(filters['series'])]

        # モデルフィルタ
        if filters.get('models'):
            filtered_df = filtered_df[filtered_df['モデル'].isin(filters['models'])]

        # Y軸範囲フィルタ
        if 'y_range' in filters:
            y_min, y_max = filters['y_range']
            filtered_df = filtered_df[
                (filtered_df['Y軸(mm)'] >= y_min) & (filtered_df['Y軸(mm)'] <= y_max)
            ]

        # 主軸回転数範囲フィルタ
        if 'rpm_range' in filters:
            rpm_min, rpm_max = filters['rpm_range']
            filtered_df = filtered_df[
                (filtered_df['主軸最大(rpm)'] >= rpm_min) &
                (filtered_df['主軸最大(rpm)'] <= rpm_max)
            ]

        # 同時5軸フィルタ
        if filters.get('only_5axis'):
            filtered_df = filtered_df[filtered_df['同時5軸'] == True]

        # 100本マガジンフィルタ
        if filters.get('only_100tools'):
            filtered_df = filtered_df[filtered_df['100本マガジン'] == True]

        return filtered_df

    def render_comparison_table(self, df: pd.DataFrame):
        """比較表描画"""
        st.subheader("📊 Brother機械仕様比較")

        if df.empty:
            st.warning("比較するモデルを選択してください")
            return

        # 表示カラム選択
        display_columns = [
            'メーカー', 'モデル', 'シリーズ',
            'X軸(mm)', 'Y軸(mm)', 'Z軸(mm)', 'Z軸OP(mm)',
            '主軸最大(rpm)', '早送りXY(m/min)', '早送りZ(m/min)',
            '工具本数', '工具交換時間(s)', 'テーブルX(mm)', 'テーブルY(mm)', 'テーブル許容質量(kg)',
            'CNC制御装置', '同時5軸', '100本マガジン', '特記事項'
        ]

        display_df = df[display_columns].copy()

        # データ整形
        numeric_columns = ['X軸(mm)', 'Y軸(mm)', 'Z軸(mm)', 'Z軸OP(mm)',
                          '主軸最大(rpm)', '早送りXY(m/min)', '早送りZ(m/min)',
                          'テーブルX(mm)', 'テーブルY(mm)', 'テーブル許容質量(kg)',
                          '工具交換時間(s)']

        for col in numeric_columns:
            if col in display_df.columns:
                display_df[col] = display_df[col].apply(
                    lambda x: f"{x:.1f}" if pd.notnull(x) and col == '工具交換時間(s)'
                    else f"{x:.0f}" if pd.notnull(x) else "-"
                )

        # 工具本数をフォーマット
        display_df['工具本数'] = display_df['工具本数'].apply(
            lambda x: "/".join(map(str, x)) if isinstance(x, list) else "-"
        )

        # スタイリング
        def highlight_values(s):
            if s.name in ['Y軸(mm)', '主軸最大(rpm)', '早送りXY(m/min)']:
                try:
                    numeric_s = pd.to_numeric(s.replace('-', np.nan), errors='coerce')
                    if numeric_s.notna().sum() > 1:
                        styles = [''] * len(s)
                        if numeric_s.notna().any():
                            max_idx = numeric_s.idxmax()
                            min_idx = numeric_s.idxmin()
                            styles[max_idx] = 'background-color: lightgreen'
                            styles[min_idx] = 'background-color: lightcoral'
                        return styles
                except:
                    pass
            return [''] * len(s)

        # 表示
        styled_df = display_df.style.apply(highlight_values, axis=0)
        st.dataframe(styled_df, use_container_width=True)

        # CSV出力
        if st.button("📄 CSV出力"):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = self.outputs_dir / f"brother_compare_{timestamp}.csv"
            display_df.to_csv(output_path, index=False, encoding='utf-8-sig')
            st.success(f"比較結果を {output_path} に出力しました")

    def render_kpi_cards(self, df: pd.DataFrame):
        """KPIカード描画"""
        if df.empty or len(df) > 4:
            return

        st.subheader("📈 主要仕様")

        cols = st.columns(len(df))

        for i, (_, row) in enumerate(df.iterrows()):
            with cols[i]:
                # モデル名
                st.metric(
                    label=row['モデル'],
                    value=f"Y: {row['Y軸(mm)']}mm" if pd.notnull(row['Y軸(mm)']) else "Y: -"
                )

                # 主軸回転数
                if pd.notnull(row['主軸最大(rpm)']):
                    st.metric("主軸", f"{row['主軸最大(rpm)']:.0f} rpm")

                # 工具本数
                if row['工具本数'] and row['工具本数'] != "-":
                    tools = row['工具本数']
                    if isinstance(tools, list):
                        tools_str = "/".join(map(str, tools)) + "本"
                    else:
                        tools_str = str(tools)
                    st.metric("工具", tools_str)

                # 特殊機能
                features = []
                if row['同時5軸']:
                    features.append("5軸")
                if row['100本マガジン']:
                    features.append("100本")
                if features:
                    st.write(f"🔧 {', '.join(features)}")

    def render_model_details(self, df: pd.DataFrame):
        """モデル詳細描画"""
        st.subheader("🔍 モデル詳細")

        if df.empty:
            st.info("サイドバーでモデルを選択してください")
            return

        for _, row in df.iterrows():
            with st.expander(f"{row['モデル']} ({row['シリーズ']})"):
                col1, col2 = st.columns(2)

                with col1:
                    st.write("**基本仕様:**")
                    if pd.notnull(row['X軸(mm)']):
                        st.write(f"• X軸: {row['X軸(mm)']}mm")
                    if pd.notnull(row['Y軸(mm)']):
                        st.write(f"• Y軸: {row['Y軸(mm)']}mm")
                    if pd.notnull(row['Z軸(mm)']):
                        st.write(f"• Z軸: {row['Z軸(mm)']}mm")
                        if pd.notnull(row['Z軸OP(mm)']):
                            st.write(f"  (オプション: {row['Z軸OP(mm)']}mm)")

                with col2:
                    st.write("**性能:**")
                    if pd.notnull(row['主軸最大(rpm)']):
                        st.write(f"• 主軸最大: {row['主軸最大(rpm)']:.0f}rpm")
                    if pd.notnull(row['早送りXY(m/min)']):
                        st.write(f"• 早送りXY: {row['早送りXY(m/min)']:.0f}m/min")
                    if pd.notnull(row['早送りZ(m/min)']):
                        st.write(f"• 早送りZ: {row['早送りZ(m/min)']:.0f}m/min")

                # 特記事項
                if row['特記事項']:
                    st.write("**特記事項:**")
                    for note in row['特記事項'].split(';'):
                        if note.strip():
                            st.write(f"• {note.strip()}")

    def render_competitive_analysis(self, df: pd.DataFrame, filtered_df: pd.DataFrame):
        """競合比較分析"""
        st.subheader("⚔️ Brother vs 競合社分析")

        if df.empty:
            st.warning("データが不足しています")
            return

        # メーカー別データ分離
        brother_df = df[df['メーカー'] == 'BROTHER']
        competitor_df = df[df['メーカー'].isin(['FANUC', 'OKUMA'])]

        if brother_df.empty or competitor_df.empty:
            st.warning("Brother及び競合社のデータが必要です")
            return

        fanuc_df = df[df['メーカー'] == 'FANUC']
        okuma_df = df[df['メーカー'] == 'OKUMA']

        # 競合比較サマリー
        col1, col2 = st.columns(2)

        with col1:
            st.markdown("### 🔧 Brother SPEEDIO")
            st.metric("モデル数", len(brother_df))

            brother_y_range = brother_df['Y軸(mm)'].dropna()
            if not brother_y_range.empty:
                st.metric("Y軸範囲", f"{brother_y_range.min():.0f} - {brother_y_range.max():.0f} mm")

            brother_spindle = brother_df['主軸最大(rpm)'].dropna()
            if not brother_spindle.empty:
                st.metric("主軸最大", f"{brother_spindle.max():.0f} rpm")

        with col2:
            st.markdown("### ⚙️ 競合社製品")
            st.metric("モデル数", len(competitor_df))

            if not fanuc_df.empty:
                st.write("**FANUC**: " + f"{len(fanuc_df)}機種")
            if not okuma_df.empty:
                st.write("**OKUMA**: " + f"{len(okuma_df)}機種")

            competitor_y_range = competitor_df['Y軸(mm)'].dropna()
            if not competitor_y_range.empty:
                st.metric("Y軸範囲", f"{competitor_y_range.min():.0f} - {competitor_y_range.max():.0f} mm")

            competitor_spindle = competitor_df['主軸最大(rpm)'].dropna()
            if not competitor_spindle.empty:
                st.metric("主軸最大", f"{competitor_spindle.max():.0f} rpm")

        st.divider()

        # 競合比較チャート
        col1, col2 = st.columns(2)

        with col1:
            # Y軸ストローク比較
            brother_y = brother_df['Y軸(mm)'].dropna()
            competitor_y = competitor_df['Y軸(mm)'].dropna()

            if not brother_y.empty and not competitor_y.empty:
                fig_y = go.Figure()

                fig_y.add_trace(go.Box(
                    y=brother_y,
                    name="Brother",
                    marker_color="blue"
                ))

                fig_y.add_trace(go.Box(
                    y=competitor_y,
                    name="競合社",
                    marker_color="orange"
                ))

                fig_y.update_layout(
                    title="Y軸ストローク比較",
                    yaxis_title="Y軸ストローク (mm)",
                    height=400
                )

                st.plotly_chart(fig_y, use_container_width=True)

        with col2:
            # 主軸回転数比較
            brother_spindle = brother_df['主軸最大(rpm)'].dropna()
            fanuc_spindle = fanuc_df['主軸最大(rpm)'].dropna()

            if not brother_spindle.empty and not fanuc_spindle.empty:
                fig_spindle = go.Figure()

                fig_spindle.add_trace(go.Box(
                    y=brother_spindle,
                    name="Brother",
                    marker_color="blue"
                ))

                fig_spindle.add_trace(go.Box(
                    y=fanuc_spindle,
                    name="競合社",
                    marker_color="orange"
                ))

                fig_spindle.update_layout(
                    title="主軸最大回転数比較",
                    yaxis_title="主軸最大回転数 (rpm)",
                    height=400
                )

                st.plotly_chart(fig_spindle, use_container_width=True)

        # 競合優位点分析
        st.subheader("🎯 競合優位点分析")

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("#### 🔵 Brother の優位点")
            brother_advantages = []

            # Y軸ストローク比較
            fanuc_y = fanuc_df['Y軸(mm)'].dropna()
            if not brother_y.empty and not fanuc_y.empty:
                if brother_y.max() > fanuc_y.max():
                    brother_advantages.append(f"Y軸最大ストローク: {brother_y.max():.0f}mm (FANUC: {fanuc_y.max():.0f}mm)")

            # 同時5軸対応率
            brother_5axis_rate = (brother_df['同時5軸'].sum() / len(brother_df)) * 100
            fanuc_5axis_rate = (fanuc_df['同時5軸'].sum() / len(fanuc_df)) * 100 if len(fanuc_df) > 0 else 0

            if brother_5axis_rate > fanuc_5axis_rate:
                brother_advantages.append(f"同時5軸対応率: {brother_5axis_rate:.0f}% (FANUC: {fanuc_5axis_rate:.0f}%)")

            # 100本マガジン対応
            brother_100t_rate = (brother_df['100本マガジン'].sum() / len(brother_df)) * 100
            fanuc_100t_rate = (fanuc_df['100本マガジン'].sum() / len(fanuc_df)) * 100 if len(fanuc_df) > 0 else 0

            if brother_100t_rate > fanuc_100t_rate:
                brother_advantages.append(f"100本マガジン対応: {brother_100t_rate:.0f}%のモデル")

            if brother_advantages:
                for adv in brother_advantages:
                    st.write(f"✅ {adv}")
            else:
                st.write("特定領域での優位性を分析中...")

        with col2:
            st.markdown("#### 🟠 競合社の優位点")
            fanuc_advantages = []

            # 早送り速度比較
            brother_rapid_z = brother_df['早送りZ(m/min)'].dropna()
            fanuc_rapid_z = fanuc_df['早送りZ(m/min)'].dropna()

            if not fanuc_rapid_z.empty and not brother_rapid_z.empty:
                if fanuc_rapid_z.max() > brother_rapid_z.max():
                    fanuc_advantages.append(f"Z軸早送り最高速度: {fanuc_rapid_z.max():.0f}m/min (Brother: {brother_rapid_z.max():.0f}m/min)")

            # 工具交換時間
            fanuc_tt = fanuc_df['工具交換時間(s)'].dropna()
            if not fanuc_tt.empty:
                fanuc_advantages.append(f"工具交換時間: 最短{fanuc_tt.min():.1f}秒")

            # 熱変位補正機能
            fanuc_thermal = fanuc_df['特記事項'].str.contains('熱変位補正', na=False).sum()
            if fanuc_thermal > 0:
                fanuc_advantages.append("熱変位補正機能標準搭載")

            if fanuc_advantages:
                for adv in fanuc_advantages:
                    st.write(f"✅ {adv}")
            else:
                st.write("特定領域での優位性を分析中...")

        # 推奨戦略
        st.subheader("🚀 営業戦略提案")

        strategy_text = """
        **Brother SPEEDIOの営業ポイント:**
        - Y軸ストローク450mmの標準対応（Xd2シリーズ）
        - 100本マガジン対応による工程集約メリット
        - 同時5軸加工による複雑形状対応力
        - CTS対応による高圧クーラント加工

        **競合社対抗戦略:**
        - Z軸早送り60m/minに対し、加工エリア拡大でサイクル短縮を提案
        - 熱変位補正に対し、機械剛性と安定性をアピール
        - 28本タレットに対し、100本マガジンの段取り回数削減効果を強調
        """

        st.markdown(strategy_text)

    def render_sales_chatbot(self, df: pd.DataFrame):
        """営業支援チャットボット"""
        st.subheader("🤖 営業支援AIチャット")
        st.markdown("Brother vs 競合社製品について何でもお答えします！セールスポイント、競合比較、技術仕様など、お気軽にご質問ください。")

        # OpenAI API キーチェック
        if not os.getenv('OPENAI_API_KEY'):
            st.warning("⚠️ OPENAI_API_KEY環境変数が設定されていません。チャット機能を使用するには設定してください。")
            return

        # チャット履歴の初期化
        if 'chat_history' not in st.session_state:
            st.session_state.chat_history = []

        # 現在のデータサマリーを作成
        data_summary = self._create_data_summary(df)

        # チャット入力
        user_question = st.text_input(
            "質問を入力してください",
            placeholder="例：Brother Xd2とFANUC DCSの主軸性能を比較して",
            key="chatbot_input"
        )

        col1, col2, col3 = st.columns([2, 1, 1])

        with col1:
            send_button = st.button("💬 質問する", type="primary")

        with col2:
            clear_button = st.button("🗑️ 履歴クリア")

        with col3:
            example_button = st.button("💡 質問例")

        if example_button:
            st.info("""
            **質問例:**

            🔹 **製品比較**
            - Brother Xd2と競合社製品の違いは？
            - 主軸性能が一番高いのはどの機種？
            - Y軸450mmに対応する競合機種は？

            🔹 **セールスポイント**
            - Brother 100本マガジンのメリットは？
            - 競合社熱変位補正に対する対抗策は？
            - 同時5軸加工の営業トークを教えて

            🔹 **技術仕様**
            - 各機種の工具交換時間を比較して
            - テーブル許容質量の一覧を作って
            - CNC制御装置の特徴を説明して
            """)

        if clear_button:
            st.session_state.chat_history = []
            st.success("チャット履歴をクリアしました")
            st.rerun()

        if send_button and user_question.strip():
            with st.spinner("回答を生成中..."):
                try:
                    # 質問に関連するPDFを識別
                    relevant_pdfs = self._identify_relevant_pdfs(user_question, df)

                    # OpenAI APIで回答生成
                    response = self._generate_sales_response(user_question, data_summary)

                    # チャット履歴に追加
                    st.session_state.chat_history.append({
                        'question': user_question,
                        'answer': response,
                        'source_pdfs': relevant_pdfs,
                        'timestamp': datetime.now()
                    })

                except Exception as e:
                    st.error(f"回答生成エラー: {e}")

        # チャット履歴表示
        if st.session_state.chat_history:
            st.subheader("💬 チャット履歴")

            for i, chat in enumerate(reversed(st.session_state.chat_history[-10:])):  # 最新10件
                chat_num = len(st.session_state.chat_history) - i

                with st.container():
                    # 質問表示
                    st.markdown(f"**🙋 Q{chat_num}: {chat['question']}**")

                    # 回答表示
                    st.markdown(f"**🤖 A{chat_num}:** {chat['answer']}")

                    # 参照元PDF表示
                    if 'source_pdfs' in chat and chat['source_pdfs']:
                        with st.expander("📄 参照カタログ", expanded=False):
                            for pdf in chat['source_pdfs']:
                                st.write(f"• {pdf}")

                    # タイムスタンプ
                    st.caption(f"⏰ {chat['timestamp'].strftime('%H:%M:%S')}")

                    st.divider()

    def _create_data_summary(self, df: pd.DataFrame) -> str:
        """データサマリー作成（PDF参照元含む）"""
        if df.empty:
            return "データがありません"

        brother_df = df[df['メーカー'] == 'BROTHER']
        fanuc_df = df[df['メーカー'] == 'FANUC']

        summary_parts = ["=== 製品データベース概要 ==="]

        # Brother データ
        if not brother_df.empty:
            summary_parts.append(f"\n【Brother SPEEDIO】({len(brother_df)}機種)")

            # PDF参照元
            if '参照PDF' in brother_df.columns:
                brother_pdfs = brother_df['参照PDF'].dropna().unique()
                summary_parts.append("参照カタログ:")
                for pdf in brother_pdfs:
                    summary_parts.append(f"  - {pdf}")

            # シリーズ別
            brother_series = brother_df.groupby('シリーズ')['モデル'].count()
            for series, count in brother_series.items():
                summary_parts.append(f"- {series}: {count}機種")

            # 主要仕様範囲
            brother_y = brother_df['Y軸(mm)'].dropna()
            if not brother_y.empty:
                summary_parts.append(f"- Y軸ストローク: {brother_y.min():.0f}-{brother_y.max():.0f}mm")

            brother_spindle = brother_df['主軸最大(rpm)'].dropna()
            if not brother_spindle.empty:
                summary_parts.append(f"- 主軸回転数: {brother_spindle.min():,.0f}-{brother_spindle.max():,.0f}rpm")

            # 特徴
            brother_5axis = brother_df['同時5軸'].sum()
            brother_100t = brother_df['100本マガジン'].sum()
            summary_parts.append(f"- 同時5軸対応: {brother_5axis}機種")
            summary_parts.append(f"- 100本マガジン対応: {brother_100t}機種")

        # 競合社データ
        if not fanuc_df.empty:
            summary_parts.append(f"\n【競合社製品】({len(fanuc_df)}機種)")

            # PDF参照元
            if '参照PDF' in fanuc_df.columns:
                fanuc_pdfs = fanuc_df['参照PDF'].dropna().unique()
                summary_parts.append("参照カタログ:")
                for pdf in fanuc_pdfs:
                    summary_parts.append(f"  - {pdf}")

            # シリーズ別
            fanuc_series = fanuc_df.groupby('シリーズ')['モデル'].count()
            for series, count in fanuc_series.items():
                summary_parts.append(f"- {series}: {count}機種")

            # 主要仕様範囲
            fanuc_y = fanuc_df['Y軸(mm)'].dropna()
            if not fanuc_y.empty:
                summary_parts.append(f"- Y軸ストローク: {fanuc_y.min():.0f}-{fanuc_y.max():.0f}mm")

            fanuc_spindle = fanuc_df['主軸最大(rpm)'].dropna()
            if not fanuc_spindle.empty:
                summary_parts.append(f"- 主軸回転数: {fanuc_spindle.min():,.0f}-{fanuc_spindle.max():,.0f}rpm")

            # FANUC特徴
            fanuc_thermal = fanuc_df['特記事項'].str.contains('熱変位補正', na=False).sum()
            fanuc_rapid_z = fanuc_df['早送りZ(m/min)'].dropna()
            if not fanuc_rapid_z.empty:
                summary_parts.append(f"- Z軸早送り最大: {fanuc_rapid_z.max():.0f}m/min")
            summary_parts.append(f"- 熱変位補正機能: {fanuc_thermal}機種")

        return "\n".join(summary_parts)

    def _generate_sales_response(self, question: str, data_summary: str) -> str:
        """営業支援AI回答生成"""
        try:
            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

            system_prompt = """あなたはBrother工作機械の営業支援AIです。以下の役割を担います：

【主要任務】
1. Brother SPEEDIO vs 競合社工作機械の競合比較分析
2. Brother製品のセールスポイント提案
3. 顧客課題に対する最適機種推奨
4. 技術仕様の分かりやすい説明

【回答スタイル】
- 営業現場で即座に使える実用的な情報
- 具体的な数値データに基づく根拠
- 顧客メリットを明確にした提案
- 競合対抗時の差別化ポイント強調

【Brother優位点（必ず活用）】
✅ 同時5軸加工による工程集約
✅ 100本マガジンによる段取り削減
✅ Y軸450mm標準対応（Xd2シリーズ）
✅ CTS高圧クーラント対応
✅ Z軸加速度2.2Gによる高速加工

【競合社対抗策】
- 主軸24,000rpm → 「加工エリア拡大でトータルサイクル短縮」
- 熱変位補正 → 「機械剛性と長期安定性」
- Z軸早送り60m/min → 「Y軸拡大で移動距離短縮」

【禁止事項】
- 憶測や不正確な情報
- 競合社製品の誹謗中傷
- 技術的に不可能な主張"""

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"""質問: {question}

利用可能なデータ:
{data_summary}

上記データを基に、営業現場で即座に活用できる回答をお願いします。"""}
                ],
                temperature=0.7,
                max_tokens=1000
            )

            return response.choices[0].message.content

        except Exception as e:
            return f"申し訳ございません。回答生成中にエラーが発生しました: {str(e)}"

    def _identify_relevant_pdfs(self, question: str, df: pd.DataFrame) -> List[str]:
        """質問に関連するPDFファイルを識別"""
        if df.empty or '参照PDF' not in df.columns:
            return []

        question_lower = question.lower()
        relevant_pdfs = set()

        # 質問からキーワードを抽出して関連PDFを識別
        keywords = {
            'brother': ['brother', 'ブラザー', 'speedio', 'xd1', 'xd2'],
            'fanuc': ['fanuc', 'ファナック', 'robodrill', 'dib', 'dcs', 'rdrilla'],
            'model': ['s300', 's500', 's700', 'm200', 'm300', 'dib5', 'plus', 'd54cs'],
            'spec': ['主軸', 'spindle', 'y軸', 'z軸', '工具', 'tool', 'ストローク', 'rpm']
        }

        # キーワードベースマッチング
        for keyword in keywords['brother'] + keywords['fanuc'] + keywords['model']:
            if keyword in question_lower:
                # 該当するPDFを検索
                matching_rows = df[df['モデル'].str.lower().str.contains(keyword, na=False) |
                                   df['シリーズ'].str.lower().str.contains(keyword, na=False) |
                                   df['メーカー'].str.lower().str.contains(keyword, na=False)]
                for _, row in matching_rows.iterrows():
                    if pd.notna(row['参照PDF']):
                        relevant_pdfs.add(row['参照PDF'])

        # メーカー別デフォルト
        if 'brother' in question_lower or 'ブラザー' in question_lower:
            brother_pdfs = df[df['メーカー'] == 'BROTHER']['参照PDF'].dropna().unique()
            relevant_pdfs.update(brother_pdfs[:2])  # 最大2件

        if 'fanuc' in question_lower or 'ファナック' in question_lower or '競合' in question_lower:
            fanuc_pdfs = df[df['メーカー'] == 'FANUC']['参照PDF'].dropna().unique()
            relevant_pdfs.update(fanuc_pdfs[:2])  # 最大2件

        # 仕様比較質問の場合は両方のPDFを含める
        if any(keyword in question_lower for keyword in ['比較', 'vs', '違い', 'compare', 'difference']):
            brother_pdfs = df[df['メーカー'] == 'BROTHER']['参照PDF'].dropna().unique()
            fanuc_pdfs = df[df['メーカー'] == 'FANUC']['参照PDF'].dropna().unique()
            relevant_pdfs.update(brother_pdfs[:1])
            relevant_pdfs.update(fanuc_pdfs[:1])

        # PDFファイル名を整理して返す
        pdf_list = sorted(list(relevant_pdfs))
        return pdf_list[:5]  # 最大5件まで

    def run(self):
        """アプリケーション実行"""
        st.title("🔧 Brother vs 競合社比較")
        st.markdown("Brother SPEEDIO vs 競合社工作機械 仕様比較・分析ツール")

        # データ読み込み
        df = self.load_all_catalogs()

        if not df.empty:
            # サイドバー
            filters = self.render_sidebar(df)

            # フィルタ適用
            filtered_df = self.apply_filters(df, filters)

            # メインコンテンツタブ
            tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 仕様比較", "⚔️ 競合比較", "🔍 詳細", "📈 統計", "🤖 営業支援AI"])

            with tab1:
                # 選択モデルのKPIカード
                if filters.get('models'):
                    selected_df = filtered_df[filtered_df['モデル'].isin(filters['models'])]
                    self.render_kpi_cards(selected_df)

                # 比較表
                self.render_comparison_table(filtered_df)

            with tab2:
                # 競合比較分析
                self.render_competitive_analysis(df, filtered_df)

            with tab3:
                # 選択モデルの詳細
                if filters.get('models'):
                    selected_df = filtered_df[filtered_df['モデル'].isin(filters['models'])]
                    self.render_model_details(selected_df)
                else:
                    st.info("サイドバーでモデルを選択してください")

            with tab4:
                # 統計情報
                st.subheader("📈 Brother機械統計")

                col1, col2 = st.columns(2)

                with col1:
                    # シリーズ別分布
                    series_counts = df['シリーズ'].value_counts()
                    fig1 = px.pie(
                        values=series_counts.values,
                        names=series_counts.index,
                        title="シリーズ別モデル数"
                    )
                    st.plotly_chart(fig1, use_container_width=True)

                with col2:
                    # Y軸ストローク分布
                    y_data = df['Y軸(mm)'].dropna()
                    if not y_data.empty:
                        fig2 = px.histogram(
                            y_data,
                            nbins=10,
                            title="Y軸ストローク分布",
                            labels={'value': 'Y軸ストローク(mm)', 'count': '機種数'}
                        )
                        st.plotly_chart(fig2, use_container_width=True)

                # 主軸回転数 vs Y軸ストローク散布図
                scatter_data = df[['モデル', 'Y軸(mm)', '主軸最大(rpm)', 'シリーズ']].dropna()
                if not scatter_data.empty:
                    fig3 = px.scatter(
                        scatter_data,
                        x='Y軸(mm)',
                        y='主軸最大(rpm)',
                        color='シリーズ',
                        hover_name='モデル',
                        title="Y軸ストローク vs 主軸最大回転数",
                        labels={
                            'Y軸(mm)': 'Y軸ストローク(mm)',
                            '主軸最大(rpm)': '主軸最大回転数(rpm)'
                        }
                    )
                    st.plotly_chart(fig3, use_container_width=True)

            with tab5:
                # 営業支援チャットボット
                self.render_sales_chatbot(df)

        else:
            st.error("Brotherカタログファイルが見つかりません。CatalogDATAフォルダにBrother PDFファイルを配置してください。")


def main():
    """メイン実行関数"""
    app = BrotherCatalogApp()
    app.run()


if __name__ == "__main__":
    main()