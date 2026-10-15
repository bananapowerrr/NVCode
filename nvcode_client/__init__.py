"""UI-independent NVCode Protocol v0 client and reference UI adapters."""
from .app_shell import AppShell
from .client import HttpRestAdapter, ProtocolClient, RetryPolicy, WebSocketConnector
from .controllers import ChatController, LogsMetricsController, QueueController, WorkersController
from .store import ClientState, EventReducer, StateStore, SyncTicket
from .ui_adapter import (
    MetricsViewModel, TaskViewModel, UiThreadDispatcher, WorkerViewModel,
    filter_tasks, filter_workers,
)

__all__ = [
    "AppShell", "ChatController", "ClientState", "EventReducer", "HttpRestAdapter",
    "LogsMetricsController", "MetricsViewModel", "ProtocolClient", "QueueController",
    "RetryPolicy", "StateStore", "SyncTicket", "TaskViewModel", "UiThreadDispatcher",
    "WebSocketConnector", "WorkerViewModel", "WorkersController", "filter_tasks",
    "filter_workers",
]
