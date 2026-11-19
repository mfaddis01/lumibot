"""Dependency-free offline research rules. No trading or adapter integration."""
from .evaluator import Signal, evaluate_signal

__all__ = ["Signal", "evaluate_signal"]
