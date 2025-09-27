from typing import Dict, List, Optional, Union, Any
from pydantic import BaseModel, Field
from datetime import datetime


class MachineMetadata(BaseModel):
    """Machine metadata information"""
    brand: str = Field(..., description="BROTHER | FANUC | OKUMA")
    series: str = Field(..., description="Machine series name")
    model: str = Field(..., description="Specific model name")
    doc_source: str = Field(..., description="Source document filename#page")
    doc_date_or_version: Optional[str] = Field(None, description="Version or date")
    cnc: Optional[str] = Field(None, description="CNC controller type")


class AxisTravel(BaseModel):
    """Axis travel specifications"""
    X_mm: Optional[float] = None
    Y_mm: Optional[float] = None
    Z_mm: Optional[float] = None
    Z_alt_mm: Optional[float] = None


class TableSpec(BaseModel):
    """Table specifications"""
    size_mm: Optional[List[float]] = None
    load_kg: Optional[float] = None


class SpindleSpec(BaseModel):
    """Spindle specifications"""
    max_rpm: Optional[float] = None
    options_rpm: Optional[List[float]] = None
    max_torque_Nm: Optional[float] = None
    note: Optional[str] = None


class FeedSpec(BaseModel):
    """Feed rate specifications"""
    rapid_XY_mpm: Optional[float] = None
    rapid_Z_mpm: Optional[float] = None


class ToolMagazine(BaseModel):
    """Tool magazine specifications"""
    tools: Optional[List[int]] = None
    tool_to_tool_s: Optional[float] = None
    chip_to_chip_s: Optional[float] = None
    max_tool_mass_kg: Optional[float] = None


class MachineSpecs(BaseModel):
    """Complete machine specifications"""
    metadata: MachineMetadata
    axis_travel: AxisTravel = Field(default_factory=AxisTravel)
    table: TableSpec = Field(default_factory=TableSpec)
    spindle: SpindleSpec = Field(default_factory=SpindleSpec)
    feed: FeedSpec = Field(default_factory=FeedSpec)
    tool_mag: ToolMagazine = Field(default_factory=ToolMagazine)
    footprint_mm: Optional[List[float]] = None
    notes: List[str] = Field(default_factory=list)


class DocumentChunk(BaseModel):
    """Document chunk for vector search"""
    content: str
    source: str
    page: int
    brand: str
    model: Optional[str] = None
    series: Optional[str] = None
    chunk_id: str


class SearchResult(BaseModel):
    """Search result with citation"""
    content: str
    source: str
    page: int
    brand: str
    model: Optional[str] = None
    score: float


class QAResponse(BaseModel):
    """QA response with citations"""
    answer: str
    citations: List[SearchResult]
    confidence: float