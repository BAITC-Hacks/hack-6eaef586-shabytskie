"""Stable contracts shared by the StockPilot UI and calculation providers."""
from dataclasses import dataclass, field
from datetime import date
from typing import Literal
import pandas as pd


@dataclass
class InputBundle:
    sales: pd.DataFrame
    inventory: pd.DataFrame
    suppliers: pd.DataFrame
    stock_history: pd.DataFrame | None = None


@dataclass(frozen=True)
class AnalysisSettings:
    as_of_date: date
    review_period_days: int = 7
    safety_stock_days: int = 7
    expected_growth_override: float | None = None
    enable_anomaly_correction: bool = True
    enable_stockout_correction: bool = True


@dataclass
class ForecastBundle:
    summary: pd.DataFrame
    daily_forecast: pd.DataFrame
    corrected_history: pd.DataFrame
    quality_report: pd.DataFrame
    metadata: dict = field(default_factory=dict)


@dataclass
class AnalysisResult:
    recommendations: pd.DataFrame
    history: pd.DataFrame = field(default_factory=pd.DataFrame)
    forecast_series: pd.DataFrame = field(default_factory=pd.DataFrame)
    quality_report: pd.DataFrame = field(default_factory=pd.DataFrame)
    metadata: dict = field(default_factory=dict)


Mode = Literal["demo", "integrated"]

