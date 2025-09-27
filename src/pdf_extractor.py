import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import pymupdf as fitz
import pdfplumber
import logging
from dataclasses import dataclass

from .models.schemas import MachineMetadata, AxisTravel, TableSpec, SpindleSpec, FeedSpec, ToolMagazine, MachineSpecs

logger = logging.getLogger(__name__)


@dataclass
class ExtractionPattern:
    """Regex patterns for extracting specifications"""
    # Brother specific patterns from PDF
    axis_travel = re.compile(r'各軸移動量.*?\n?((?:\S+\s+)?X(\d+)\s+Y(\d+)\s+Z(\d+(?:/\d+)?(?:※\d+)?)).*?(?:\n|$)', re.IGNORECASE | re.MULTILINE)
    axis_line = re.compile(r'X(\d+)\s+Y(\d+)\s+Z(\d+(?:/\d+)?)', re.IGNORECASE)

    # FANUC patterns
    fanuc_axis = re.compile(r'X[=\s]*(\d+)\s*Y[=\s]*(\d+)\s*Z[=\s]*(\d+)', re.IGNORECASE)

    # Spindle patterns - more specific for Brother format
    spindle_max = re.compile(r'主軸最高回転数.*?(\d{4,5})', re.IGNORECASE | re.DOTALL)
    spindle_simple = re.compile(r'(\d{4,5})\s*(?:min-1|rpm|r/min)', re.IGNORECASE)
    spindle_options = re.compile(r'オプション[：:\s]*(\d{4,5})(?:高トルク)?[、,]?\s*(\d{4,5})?[、,]?\s*(\d{4,5})?[、,]?\s*(\d{4,5})?', re.IGNORECASE)

    # Feed patterns - specific to "X/Y/Z 50/50/56" format
    rapid_feed = re.compile(r'早送り速度.*?X/Y/Z\s+(\d+)/(\d+)/(\d+)', re.IGNORECASE | re.DOTALL)

    # Tool count patterns - specific format
    tool_count_line = re.compile(r'工具本数.*?\n(.*?(\d+(?:/\d+)*(?:/\d+)*).*?本)', re.IGNORECASE | re.MULTILINE)
    tool_simple = re.compile(r'(\d+)/(\d+)(?:/(\d+))?(?:/(\d+))?\s*本', re.IGNORECASE)
    tool_100 = re.compile(r'100\s*本', re.IGNORECASE)
    tool_to_tool = re.compile(r'Tool.*?to.*?Tool.*?(\d+\.?\d*)\s*s', re.IGNORECASE)
    chip_to_chip = re.compile(r'Chip.*?to.*?Chip.*?(\d+\.?\d*)\s*s', re.IGNORECASE)

    # Table patterns
    table_size = re.compile(r'所要床面.*?(\d{1,4})\s*×\s*(\d{1,4})', re.IGNORECASE | re.DOTALL)
    table_load = re.compile(r'(?:許容質量|最大積載|テーブル.*?質量).*?(\d{2,4})\s*kg', re.IGNORECASE | re.DOTALL)

    # CNC patterns
    cnc_controller = re.compile(r'CNC-D00|FANUC\s+(?:Series\s+)?31i-B5?\s*Plus?|OSP-[A-Z0-9]+', re.IGNORECASE)


class PDFExtractor:
    """Extract machine specifications from PDF catalogs"""

    def __init__(self):
        self.patterns = ExtractionPattern()

    def normalize_text(self, text: str) -> str:
        """Normalize text using NFKC to handle full-width/half-width characters"""
        return unicodedata.normalize('NFKC', text)

    def extract_brand_series_model(self, filename: str, text: str) -> Tuple[str, str, str]:
        """Extract brand, series, and model from filename and text content"""
        filename_lower = filename.lower()

        # Brand detection
        if 'brother' in filename_lower:
            brand = 'BROTHER'
        elif 'fanac' in filename_lower or 'fanuc' in filename_lower:
            brand = 'FANUC'
        elif 'okuma' in filename_lower:
            brand = 'OKUMA'
        else:
            brand = 'UNKNOWN'

        # Series and model extraction based on brand
        series, model = self._extract_series_model_by_brand(brand, filename, text)

        return brand, series, model

    def _extract_series_model_by_brand(self, brand: str, filename: str, text: str) -> Tuple[str, str]:
        """Extract series and model based on brand"""
        if brand == 'BROTHER':
            return self._extract_brother_series_model(filename, text)
        elif brand == 'FANUC':
            return self._extract_fanuc_series_model(filename, text)
        elif brand == 'OKUMA':
            return self._extract_okuma_series_model(filename, text)
        else:
            return 'UNKNOWN', 'UNKNOWN'

    def _extract_brother_series_model(self, filename: str, text: str) -> Tuple[str, str]:
        """Extract Brother series and model"""
        if 'xd1' in filename.lower():
            series = 'SPEEDIO Xd1'
            # Extract model like S300Xd1, S500Xd1, S700Xd1
            model_match = re.search(r'(S\d{3}Xd1|M\d{3}Xd1)', filename, re.IGNORECASE)
            model = model_match.group(1).upper() if model_match else 'Xd1'
        elif 'xd2' in filename.lower():
            series = 'SPEEDIO Xd2'
            model_match = re.search(r'(S\d{3}Xd2|M\d{3}Xd2)', filename, re.IGNORECASE)
            model = model_match.group(1).upper() if model_match else 'Xd2'
        elif 'x3' in filename.lower():
            series = 'SPEEDIO X3'
            model_match = re.search(r'(S\d{3}X3|M\d{3}X3)', filename, re.IGNORECASE)
            model = model_match.group(1).upper() if model_match else 'X3'
        else:
            series = 'SPEEDIO'
            model = 'SPEEDIO'

        return series, model

    def _extract_fanuc_series_model(self, filename: str, text: str) -> Tuple[str, str]:
        """Extract FANUC series and model"""
        if 'dcs' in filename.lower():
            series = 'ROBODRILL DCS'
            # Extract D54CS, D74CS
            model_match = re.search(r'(D\d{2}CS)', text, re.IGNORECASE)
            model = model_match.group(1).upper() if model_match else 'DCS'
        elif 'dib5plus' in filename.lower():
            series = 'ROBODRILL DiB5 Plus'
            model = 'α-DiB5 Plus'
        elif 'dibplus' in filename.lower():
            series = 'ROBODRILL DiB Plus'
            model = 'α-DiB Plus'
        else:
            series = 'ROBODRILL'
            model = 'ROBODRILL'

        return series, model

    def _extract_okuma_series_model(self, filename: str, text: str) -> Tuple[str, str]:
        """Extract OKUMA series and model (placeholder for future)"""
        return 'OKUMA', 'OKUMA'

    def extract_specifications(self, pdf_path: Path) -> List[MachineSpecs]:
        """Extract machine specifications from PDF"""
        try:
            specs_list = []

            # Extract text using PyMuPDF
            doc = fitz.open(str(pdf_path))
            full_text = ""
            page_texts = {}

            for page_num in range(len(doc)):
                page = doc[page_num]
                text = page.get_text()
                normalized_text = self.normalize_text(text)
                full_text += normalized_text + "\n"
                page_texts[page_num + 1] = normalized_text

            doc.close()

            # Extract brand, series, model
            brand, series, model = self.extract_brand_series_model(pdf_path.name, full_text)

            # Create metadata
            metadata = MachineMetadata(
                brand=brand,
                series=series,
                model=model,
                doc_source=f"{pdf_path.name}",
                cnc=self._extract_cnc_controller(full_text)
            )

            # Extract specifications
            axis_travel = self._extract_axis_travel(full_text)
            table_spec = self._extract_table_spec(full_text)
            spindle_spec = self._extract_spindle_spec(full_text)
            feed_spec = self._extract_feed_spec(full_text)
            tool_mag = self._extract_tool_magazine(full_text)
            notes = self._extract_notes(full_text, brand)

            # Create machine specs
            machine_spec = MachineSpecs(
                metadata=metadata,
                axis_travel=axis_travel,
                table=table_spec,
                spindle=spindle_spec,
                feed=feed_spec,
                tool_mag=tool_mag,
                notes=notes
            )

            specs_list.append(machine_spec)

            # Log extraction success
            logger.info(f"Extracted specs for {brand} {series} {model} from {pdf_path.name}")

            return specs_list

        except Exception as e:
            logger.error(f"Error extracting from {pdf_path}: {e}")
            return []

    def _extract_axis_travel(self, text: str) -> AxisTravel:
        """Extract axis travel specifications"""
        axis = AxisTravel()

        # Try Brother specific axis pattern
        axis_match = self.patterns.axis_travel.search(text)
        if axis_match:
            try:
                # Extract X, Y, Z values
                axis.X_mm = float(axis_match.group(2))
                axis.Y_mm = float(axis_match.group(3))

                # Handle Z value which might have /alternative format
                z_value = axis_match.group(4)
                if '/' in z_value:
                    z_parts = z_value.split('/')
                    axis.Z_mm = float(z_parts[0])
                    axis.Z_alt_mm = float(z_parts[1].replace('※', '').replace('4', '380'))  # Handle ※4 notation
                else:
                    axis.Z_mm = float(z_value.replace('※', '').replace('4', ''))
            except (IndexError, ValueError, TypeError):
                pass

        # Try simple line pattern if first didn't work
        if not axis.X_mm:
            line_match = self.patterns.axis_line.search(text)
            if line_match:
                try:
                    axis.X_mm = float(line_match.group(1))
                    axis.Y_mm = float(line_match.group(2))
                    z_value = line_match.group(3)
                    if '/' in z_value:
                        z_parts = z_value.split('/')
                        axis.Z_mm = float(z_parts[0])
                        axis.Z_alt_mm = float(z_parts[1])
                    else:
                        axis.Z_mm = float(z_value)
                except (IndexError, ValueError, TypeError):
                    pass

        # Try FANUC pattern
        if not axis.X_mm:
            fanuc_match = self.patterns.fanuc_axis.search(text)
            if fanuc_match:
                try:
                    axis.X_mm = float(fanuc_match.group(1))
                    axis.Y_mm = float(fanuc_match.group(2))
                    axis.Z_mm = float(fanuc_match.group(3))
                except (ValueError, TypeError):
                    pass

        return axis

    def _extract_table_spec(self, text: str) -> TableSpec:
        """Extract table specifications"""
        table = TableSpec()

        # Table size
        size_match = self.patterns.table_size.search(text)
        if size_match:
            try:
                table.size_mm = [float(size_match.group(1)), float(size_match.group(2))]
            except (ValueError, TypeError):
                pass

        # Table load capacity
        load_match = self.patterns.table_load.search(text)
        if load_match:
            try:
                table.load_kg = float(load_match.group(1))
            except (ValueError, TypeError):
                pass

        return table

    def _extract_spindle_spec(self, text: str) -> SpindleSpec:
        """Extract spindle specifications"""
        spindle = SpindleSpec()

        # Maximum RPM - try main pattern first
        max_rpm_match = self.patterns.spindle_max.search(text)
        if max_rpm_match:
            try:
                spindle.max_rpm = float(max_rpm_match.group(1))
            except (ValueError, TypeError, IndexError):
                pass

        # If not found, try simple pattern
        if not spindle.max_rpm:
            simple_match = self.patterns.spindle_simple.search(text)
            if simple_match:
                try:
                    spindle.max_rpm = float(simple_match.group(1))
                except (ValueError, TypeError):
                    pass

        # Optional RPMs
        options_match = self.patterns.spindle_options.search(text)
        if options_match:
            try:
                options = []
                for i in range(1, 4):
                    if options_match.group(i):
                        options.append(float(options_match.group(i)))
                if options:
                    spindle.options_rpm = options
            except (ValueError, TypeError):
                pass

        return spindle

    def _extract_feed_spec(self, text: str) -> FeedSpec:
        """Extract feed rate specifications"""
        feed = FeedSpec()

        # Try Brother specific pattern: "X/Y/Z 50/50/56"
        rapid_match = self.patterns.rapid_feed.search(text)
        if rapid_match:
            try:
                feed.rapid_XY_mpm = float(rapid_match.group(1))  # X and Y are the same
                feed.rapid_Z_mpm = float(rapid_match.group(3))   # Z is the third value
            except (IndexError, ValueError, TypeError):
                pass

        return feed

    def _extract_tool_magazine(self, text: str) -> ToolMagazine:
        """Extract tool magazine specifications"""
        tool_mag = ToolMagazine()

        # Try Brother specific tool count pattern
        tool_line_match = self.patterns.tool_count_line.search(text)
        if tool_line_match:
            tool_line = tool_line_match.group(1)
            # Extract numbers like "14/21" or "14/21/28"
            simple_match = self.patterns.tool_simple.search(tool_line)
            if simple_match:
                try:
                    tools = []
                    for i in range(1, 5):
                        if simple_match.group(i):
                            tools.append(int(simple_match.group(i)))
                    if tools:
                        tool_mag.tools = tools
                except (ValueError, TypeError):
                    pass

        # Check for 100-tool magazine specifically
        if self.patterns.tool_100.search(text):
            if tool_mag.tools:
                # Add 100 to existing list if not already there
                if 100 not in tool_mag.tools:
                    tool_mag.tools.append(100)
            else:
                tool_mag.tools = [100]

        # Tool-to-tool time
        tt_match = self.patterns.tool_to_tool.search(text)
        if tt_match:
            try:
                tool_mag.tool_to_tool_s = float(tt_match.group(1))
            except (ValueError, TypeError):
                pass

        # Chip-to-chip time
        ctc_match = self.patterns.chip_to_chip.search(text)
        if ctc_match:
            try:
                tool_mag.chip_to_chip_s = float(ctc_match.group(1))
            except (ValueError, TypeError):
                pass

        return tool_mag

    def _extract_cnc_controller(self, text: str) -> Optional[str]:
        """Extract CNC controller information"""
        cnc_match = self.patterns.cnc_controller.search(text)
        if cnc_match:
            return cnc_match.group(0)
        return None

    def _extract_notes(self, text: str, brand: str) -> List[str]:
        """Extract additional notes based on brand-specific features"""
        notes = []

        if brand == 'BROTHER':
            if 'CTS' in text:
                notes.append('CTS 3/7MPa対応')
            if '同時5軸' in text or '5AX' in text:
                notes.append('同時5軸仕様有')
            if '2.2G' in text:
                notes.append('Z軸加速度 最大2.2G')
            if '100本' in text:
                notes.append('100本マガジン仕様有')

        elif brand == 'FANUC':
            if '熱変位補正' in text:
                notes.append('熱変位補正機能')
            if '省エネ' in text:
                notes.append('省エネ機能')
            if '2.2G' in text:
                notes.append('最大加速度2.2G')
            if 'Y=500' in text:
                notes.append('Y軸500mm仕様')

        return notes


def load_and_extract_pdfs(catalog_dir: Path) -> List[MachineSpecs]:
    """Load and extract all PDFs from catalog directory"""
    extractor = PDFExtractor()
    all_specs = []

    pdf_files = list(catalog_dir.glob("*.pdf"))

    for pdf_path in pdf_files:
        logger.info(f"Processing {pdf_path.name}")
        specs = extractor.extract_specifications(pdf_path)
        all_specs.extend(specs)

    return all_specs