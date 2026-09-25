"""ORM-модели CryptisDchat. Импорт всех модулей регистрирует таблицы в Base.metadata."""
from .admin import AdminAuditLog, AdminUser, Report
from .base import Base, new_uuid, utcnow
from .chain import ChainBatch, ChainEvent
from .keys import ConversationKey, KeyBackup, KeyBackupShare, KeyRecoveryRequest, UserKeySet
from .message import Attachment, Message, MessageSearchToken
from .settings import BlockedUser, GroupInviteAllow, UserSettings
from .thread import GroupKeyCommit, GroupMembershipEvent, GroupTreeNode, Thread, ThreadMember
from .user import Device, PushToken, RefreshToken, User, UserWallet

__all__ = [
    "AdminAuditLog", "AdminUser", "Attachment", "Base", "BlockedUser", "ChainBatch", "ChainEvent",
    "ConversationKey", "Device", "GroupInviteAllow", "GroupKeyCommit", "GroupMembershipEvent",
    "GroupTreeNode", "KeyBackup", "KeyBackupShare", "KeyRecoveryRequest", "Message",
    "MessageSearchToken", "PushToken", "RefreshToken", "Report", "Thread", "ThreadMember", "User",
    "UserKeySet", "UserSettings", "UserWallet", "new_uuid", "utcnow",
]
