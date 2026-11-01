"""ORM-модели CryptisDchat. Импорт всех модулей регистрирует таблицы в Base.metadata."""
from .base import Base, new_uuid, utcnow
from .chain import ChainBatch, ChainEvent
from .device_pairing import DevicePairingQuota
from .keys import ConversationKey, KeyBackup, KeyBackupShare, KeyRecoveryRequest, UserKeySet
from .message import Attachment, Message, MessageReaction, MessageSearchToken
from .report import Report
from .settings import BlockedUser, GroupInviteAllow, UserSettings
from .thread import GroupKeyCommit, GroupMembershipEvent, GroupTreeNode, Thread, ThreadMember
from .user import Device, PushToken, RefreshToken, User, UserWallet

__all__ = [
    "Attachment", "Base", "BlockedUser", "ChainBatch", "ChainEvent",
    "ConversationKey", "Device", "DevicePairingQuota", "GroupInviteAllow", "GroupKeyCommit", "GroupMembershipEvent",
    "GroupTreeNode", "KeyBackup", "KeyBackupShare", "KeyRecoveryRequest", "Message", "MessageReaction",
    "MessageSearchToken", "PushToken", "RefreshToken", "Report", "Thread", "ThreadMember", "User",
    "UserKeySet", "UserSettings", "UserWallet", "new_uuid", "utcnow",
]
