SET NAMES utf8mb4;
SET time_zone = '+00:00';

CREATE DATABASE IF NOT EXISTS cryptisdchat CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE cryptisdchat;

CREATE TABLE users (
    id                   CHAR(36)     CHARACTER SET ascii NOT NULL,
    display_name         VARCHAR(50)  NOT NULL DEFAULT '',
    username             VARCHAR(32)  NULL,
    bio                  VARCHAR(160) NOT NULL DEFAULT '',
    avatar_upload_id     CHAR(36)     CHARACTER SET ascii NULL,
    avatar_tint          VARCHAR(32)  NOT NULL DEFAULT 'hsl(204 60% 45%)',
    is_suspended         BOOLEAN      NOT NULL DEFAULT FALSE,
    suspended_reason     VARCHAR(255) NULL,
    sessions_revoked_at  DATETIME(6)  NULL,
    last_seen_at         DATETIME(6)  NULL,
    created_at           DATETIME(6)  NOT NULL,
    updated_at           DATETIME(6)  NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_username (username)
) ENGINE=InnoDB;

CREATE TABLE user_wallets (
    id                CHAR(36)    CHARACTER SET ascii NOT NULL,
    user_id           CHAR(36)    CHARACTER SET ascii NOT NULL,
    address           VARCHAR(80) CHARACTER SET ascii NOT NULL,
    friendly_address  VARCHAR(64) CHARACTER SET ascii NOT NULL DEFAULT '',
    public_key        CHAR(64)    CHARACTER SET ascii NOT NULL,
    network           VARCHAR(8)  CHARACTER SET ascii NOT NULL DEFAULT '-239',
    is_primary        BOOLEAN     NOT NULL DEFAULT TRUE,
    linked_at         DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_user_wallets_address (address),
    KEY ix_user_wallets_user_id (user_id),
    CONSTRAINT fk_user_wallets_user_id FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE devices (
    id                CHAR(36)     CHARACTER SET ascii NOT NULL,
    user_id           CHAR(36)     CHARACTER SET ascii NOT NULL,
    name              VARCHAR(100) NOT NULL DEFAULT 'Web browser',
    user_agent        VARCHAR(255) NOT NULL DEFAULT '',
    ip_address        VARCHAR(45)  CHARACTER SET ascii NOT NULL DEFAULT '',
    created_at        DATETIME(6)  NOT NULL,
    last_seen_at      DATETIME(6)  NOT NULL,
    revoked_at        DATETIME(6)  NULL,
    client_key_hash   CHAR(64)     CHARACTER SET ascii NULL,
    tokens_revoked_at DATETIME(6)  NULL,
    PRIMARY KEY (id),
    KEY ix_devices_user_id (user_id),
    CONSTRAINT fk_devices_user_id FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE refresh_tokens (
    id              CHAR(36)    CHARACTER SET ascii NOT NULL,
    user_id         CHAR(36)    CHARACTER SET ascii NOT NULL,
    device_id       CHAR(36)    CHARACTER SET ascii NOT NULL,
    token_hash      CHAR(64)    CHARACTER SET ascii NOT NULL,
    created_at      DATETIME(6) NOT NULL,
    expires_at      DATETIME(6) NOT NULL,
    last_used_at    DATETIME(6) NULL,
    revoked_at      DATETIME(6) NULL,
    replaced_by_id  CHAR(36)    CHARACTER SET ascii NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_refresh_tokens_token_hash (token_hash),
    KEY ix_refresh_tokens_user_id (user_id),
    KEY ix_refresh_tokens_device_id (device_id),
    CONSTRAINT fk_refresh_tokens_user_id FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_refresh_tokens_device_id FOREIGN KEY (device_id) REFERENCES devices (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE push_tokens (
    id          CHAR(36)     CHARACTER SET ascii NOT NULL,
    user_id     CHAR(36)     CHARACTER SET ascii NOT NULL,
    device_id   CHAR(36)     CHARACTER SET ascii NOT NULL,
    platform    ENUM('fcm','apns','webpush') NOT NULL,
    token       VARCHAR(512) CHARACTER SET ascii NOT NULL,
    created_at  DATETIME(6)  NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_push_tokens_device_platform (device_id, platform),
    KEY ix_push_tokens_user_id (user_id),
    CONSTRAINT fk_push_tokens_user_id FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_push_tokens_device_id FOREIGN KEY (device_id) REFERENCES devices (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE threads (
    id                    CHAR(36)    CHARACTER SET ascii NOT NULL,
    kind                  ENUM('direct','group') NOT NULL,
    title                 VARCHAR(80) NULL,
    avatar_upload_id      CHAR(36)    CHARACTER SET ascii NULL,
    avatar_tint           VARCHAR(32) NOT NULL DEFAULT 'hsl(265 40% 48%)',
    direct_key            VARCHAR(73) CHARACTER SET ascii NULL,
    created_by            CHAR(36)    CHARACTER SET ascii NOT NULL,
    created_at            DATETIME(6) NOT NULL,
    updated_at            DATETIME(6) NOT NULL,
    last_message_at       DATETIME(6) NULL,
    last_seq              BIGINT      NOT NULL DEFAULT 0,
    disappearing_seconds  INT         NULL,
    screenshot_block      BOOLEAN     NOT NULL DEFAULT FALSE,
    key_epoch             INT         NOT NULL DEFAULT 0,
    tree_leaf_capacity    INT         NOT NULL DEFAULT 0,
    rotation_pending      BOOLEAN     NOT NULL DEFAULT FALSE,
    PRIMARY KEY (id),
    UNIQUE KEY uq_threads_direct_key (direct_key),
    KEY ix_threads_last_message_at (last_message_at),
    CONSTRAINT fk_threads_created_by FOREIGN KEY (created_by) REFERENCES users (id)
) ENGINE=InnoDB;

CREATE TABLE thread_members (
    thread_id           CHAR(36)    CHARACTER SET ascii NOT NULL,
    user_id             CHAR(36)    CHARACTER SET ascii NOT NULL,
    role                ENUM('owner','admin','member') NOT NULL DEFAULT 'member',
    joined_at           DATETIME(6) NOT NULL,
    left_at             DATETIME(6) NULL,
    muted               BOOLEAN     NOT NULL DEFAULT FALSE,
    last_delivered_seq  BIGINT      NOT NULL DEFAULT 0,
    last_read_seq       BIGINT      NOT NULL DEFAULT 0,
    cleared_seq         BIGINT      NOT NULL DEFAULT 0,
    hidden_at           DATETIME(6) NULL,
    joined_seq          BIGINT      NOT NULL DEFAULT 0,
    leaf_index          INT         NULL,
    PRIMARY KEY (thread_id, user_id),
    KEY ix_thread_members_user_id (user_id),
    CONSTRAINT fk_thread_members_thread_id FOREIGN KEY (thread_id) REFERENCES threads (id) ON DELETE CASCADE,
    CONSTRAINT fk_thread_members_user_id FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE group_membership_events (
    id               CHAR(36)    CHARACTER SET ascii NOT NULL,
    thread_id        CHAR(36)    CHARACTER SET ascii NOT NULL,
    actor_id         CHAR(36)    CHARACTER SET ascii NOT NULL,
    action           ENUM('create','add','remove','leave','promote','demote') NOT NULL,
    subject_user_id  CHAR(36)    CHARACTER SET ascii NULL,
    leaf_index       INT         NULL,
    event_hash       CHAR(64)    CHARACTER SET ascii NOT NULL,
    chain_status     ENUM('pending','submitted','confirmed','failed') NOT NULL DEFAULT 'pending',
    tx_hash          VARCHAR(128) CHARACTER SET ascii NULL,
    attempts         INT         NOT NULL DEFAULT 0,
    last_error       TEXT        NULL,
    rotation_status  ENUM('waiting','requested','done') NOT NULL DEFAULT 'waiting',
    created_at       DATETIME(6) NOT NULL,
    confirmed_at     DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_group_membership_events_event_hash (event_hash),
    KEY ix_group_membership_events_thread_id (thread_id),
    CONSTRAINT fk_group_membership_events_thread_id FOREIGN KEY (thread_id) REFERENCES threads (id) ON DELETE CASCADE,
    CONSTRAINT fk_group_membership_events_actor_id FOREIGN KEY (actor_id) REFERENCES users (id)
) ENGINE=InnoDB;

CREATE TABLE group_tree_nodes (
    thread_id      CHAR(36)    CHARACTER SET ascii NOT NULL,
    node_index     INT         NOT NULL,
    public_key     TEXT        CHARACTER SET ascii NULL,
    owner_user_id  CHAR(36)    CHARACTER SET ascii NULL,
    epoch          INT         NOT NULL DEFAULT 0,
    updated_at     DATETIME(6) NOT NULL,
    PRIMARY KEY (thread_id, node_index),
    CONSTRAINT fk_group_tree_nodes_thread_id FOREIGN KEY (thread_id) REFERENCES threads (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE group_key_commits (
    id                   CHAR(36)    CHARACTER SET ascii NOT NULL,
    thread_id            CHAR(36)    CHARACTER SET ascii NOT NULL,
    epoch                INT         NOT NULL,
    committer_id         CHAR(36)    CHARACTER SET ascii NOT NULL,
    reason               ENUM('create','add','remove','leave','periodic') NOT NULL,
    membership_event_id  CHAR(36)    CHARACTER SET ascii NULL,
    payload_json         LONGTEXT    NOT NULL,
    created_at           DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_group_key_commits_thread_epoch (thread_id, epoch),
    CONSTRAINT fk_group_key_commits_thread_id FOREIGN KEY (thread_id) REFERENCES threads (id) ON DELETE CASCADE,
    CONSTRAINT fk_group_key_commits_committer_id FOREIGN KEY (committer_id) REFERENCES users (id)
) ENGINE=InnoDB;

CREATE TABLE user_key_sets (
    id            CHAR(36)    CHARACTER SET ascii NOT NULL,
    user_id       CHAR(36)    CHARACTER SET ascii NOT NULL,
    version       INT         NOT NULL,
    identity_pub  TEXT        CHARACTER SET ascii NOT NULL,
    signing_pub   TEXT        CHARACTER SET ascii NOT NULL,
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_user_key_sets_user_version (user_id, version),
    CONSTRAINT fk_user_key_sets_user_id FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE conversation_keys (
    id                     CHAR(36)    CHARACTER SET ascii NOT NULL,
    thread_id              CHAR(36)    CHARACTER SET ascii NOT NULL,
    epoch                  INT         NOT NULL,
    recipient_id           CHAR(36)    CHARACTER SET ascii NOT NULL,
    recipient_key_version  INT         NOT NULL,
    wrapped_key            TEXT        CHARACTER SET ascii NOT NULL,
    created_by             CHAR(36)    CHARACTER SET ascii NOT NULL,
    created_at             DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_conversation_keys_thread_epoch_recipient (thread_id, epoch, recipient_id),
    CONSTRAINT fk_conversation_keys_thread_id FOREIGN KEY (thread_id) REFERENCES threads (id) ON DELETE CASCADE,
    CONSTRAINT fk_conversation_keys_recipient_id FOREIGN KEY (recipient_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_conversation_keys_created_by FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE key_backups (
    user_id         CHAR(36)    CHARACTER SET ascii NOT NULL,
    version         INT         NOT NULL DEFAULT 1,
    ciphertext      MEDIUMBLOB  NOT NULL,
    kdf_salt        VARCHAR(64) CHARACTER SET ascii NOT NULL,
    kdf_iterations  INT         NOT NULL,
    threshold       INT         NOT NULL DEFAULT 2,
    share_count     INT         NOT NULL DEFAULT 3,
    status          ENUM('pending','active','destroyed') NOT NULL DEFAULT 'pending',
    created_at      DATETIME(6) NOT NULL,
    updated_at      DATETIME(6) NOT NULL,
    PRIMARY KEY (user_id),
    CONSTRAINT fk_key_backups_user_id FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE key_backup_shares (
    user_id         CHAR(36)     CHARACTER SET ascii NOT NULL,
    realm_index     INT          NOT NULL,
    backup_version  INT          NOT NULL,
    status          ENUM('pending','stored','failed','destroyed') NOT NULL DEFAULT 'pending',
    last_error      VARCHAR(255) NULL,
    updated_at      DATETIME(6)  NOT NULL,
    PRIMARY KEY (user_id, realm_index),
    CONSTRAINT fk_key_backup_shares_user_id FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE key_recovery_requests (
    id             CHAR(36)    CHARACTER SET ascii NOT NULL,
    user_id        CHAR(36)    CHARACTER SET ascii NOT NULL,
    device_id      CHAR(36)    CHARACTER SET ascii NOT NULL,
    status         ENUM('pending','done','wrong_pin','destroyed','failed') NOT NULL DEFAULT 'pending',
    result_json    MEDIUMTEXT  NULL,
    attempts_left  INT         NULL,
    created_at     DATETIME(6) NOT NULL,
    finished_at    DATETIME(6) NULL,
    PRIMARY KEY (id),
    KEY ix_key_recovery_requests_user_id (user_id),
    CONSTRAINT fk_key_recovery_requests_user_id FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE messages (
    id                  CHAR(36)      CHARACTER SET ascii NOT NULL,
    thread_id           CHAR(36)      CHARACTER SET ascii NOT NULL,
    seq                 BIGINT        NOT NULL,
    sender_id           CHAR(36)      CHARACTER SET ascii NOT NULL,
    client_msg_id       CHAR(36)      CHARACTER SET ascii NOT NULL,
    kind                ENUM('text','file','system') NOT NULL DEFAULT 'text',
    ciphertext          MEDIUMBLOB    NOT NULL,
    nonce               VARBINARY(24) NOT NULL,
    signature           VARBINARY(96) NOT NULL,
    key_epoch           INT           NOT NULL,
    sender_key_version  INT           NOT NULL,
    content_hash        CHAR(64)      CHARACTER SET ascii NOT NULL,
    reply_to_id         CHAR(36)      CHARACTER SET ascii NULL,
    forwarded_from_id   CHAR(36)      CHARACTER SET ascii NULL,
    attachment_id       CHAR(36)      CHARACTER SET ascii NULL,
    created_at          DATETIME(6)   NOT NULL,
    expires_at          DATETIME(6)   NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_messages_thread_seq (thread_id, seq),
    UNIQUE KEY uq_messages_sender_client_msg (sender_id, client_msg_id),
    KEY ix_messages_expires_at (expires_at),
    CONSTRAINT fk_messages_thread_id FOREIGN KEY (thread_id) REFERENCES threads (id) ON DELETE CASCADE,
    CONSTRAINT fk_messages_sender_id FOREIGN KEY (sender_id) REFERENCES users (id)
) ENGINE=InnoDB;

CREATE TABLE message_search_tokens (
    message_id  CHAR(36) CHARACTER SET ascii NOT NULL,
    token       CHAR(32) CHARACTER SET ascii NOT NULL,
    thread_id   CHAR(36) CHARACTER SET ascii NOT NULL,
    PRIMARY KEY (message_id, token),
    KEY ix_message_search_tokens_thread_token (thread_id, token),
    CONSTRAINT fk_message_search_tokens_message_id FOREIGN KEY (message_id) REFERENCES messages (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE attachments (
    id            CHAR(36)     CHARACTER SET ascii NOT NULL,
    owner_id      CHAR(36)     CHARACTER SET ascii NOT NULL,
    kind          ENUM('file','thumbnail','avatar','group_avatar') NOT NULL DEFAULT 'file',
    thread_id     CHAR(36)     CHARACTER SET ascii NULL,
    message_id    CHAR(36)     CHARACTER SET ascii NULL,
    thumbnail_id  CHAR(36)     CHARACTER SET ascii NULL,
    storage_path  VARCHAR(255) NOT NULL,
    size_bytes    BIGINT       NOT NULL,
    content_type  VARCHAR(100) NOT NULL DEFAULT 'application/octet-stream',
    created_at    DATETIME(6)  NOT NULL,
    deleted_at    DATETIME(6)  NULL,
    PRIMARY KEY (id),
    KEY ix_attachments_owner_created (owner_id, created_at),
    CONSTRAINT fk_attachments_owner_id FOREIGN KEY (owner_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE chain_batches (
    id            CHAR(36)     CHARACTER SET ascii NOT NULL,
    merkle_root   CHAR(64)     CHARACTER SET ascii NOT NULL,
    leaf_count    INT          NOT NULL,
    status        ENUM('pending','submitted','confirmed','failed') NOT NULL DEFAULT 'pending',
    tx_hash       VARCHAR(128) CHARACTER SET ascii NULL,
    tx_lt         BIGINT       NULL,
    attempts      INT          NOT NULL DEFAULT 0,
    last_error    TEXT         NULL,
    created_at    DATETIME(6)  NOT NULL,
    submitted_at  DATETIME(6)  NULL,
    confirmed_at  DATETIME(6)  NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_chain_batches_merkle_root (merkle_root),
    KEY ix_chain_batches_status (status)
) ENGINE=InnoDB;

CREATE TABLE chain_events (
    id            CHAR(36)    CHARACTER SET ascii NOT NULL,
    event_type    ENUM('message.sent','message.delivered','message.read') NOT NULL,
    thread_id     CHAR(36)    CHARACTER SET ascii NOT NULL,
    message_id    CHAR(36)    CHARACTER SET ascii NULL,
    actor_id      CHAR(36)    CHARACTER SET ascii NOT NULL,
    up_to_seq     BIGINT      NULL,
    event_hash    CHAR(64)    CHARACTER SET ascii NOT NULL,
    status        ENUM('queued','batched','confirmed') NOT NULL DEFAULT 'queued',
    batch_id      CHAR(36)    CHARACTER SET ascii NULL,
    leaf_index    INT         NULL,
    merkle_proof  TEXT        NULL,
    created_at    DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_chain_events_event_hash (event_hash),
    KEY ix_chain_events_message_id (message_id),
    KEY ix_chain_events_thread_type_seq (thread_id, event_type, up_to_seq),
    KEY ix_chain_events_batch_id (batch_id)
) ENGINE=InnoDB;

CREATE TABLE user_settings (
    user_id              CHAR(36)    CHARACTER SET ascii NOT NULL,
    autolock             ENUM('1m','5m','30m','1h','never') NOT NULL DEFAULT '1m',
    group_invite_policy  ENUM('everyone','nobody','allowlist') NOT NULL DEFAULT 'everyone',
    markdown_preview     BOOLEAN     NOT NULL DEFAULT FALSE,
    media_retention      ENUM('keep','30d','60d','6m') NOT NULL DEFAULT '30d',
    show_online          BOOLEAN     NOT NULL DEFAULT TRUE,
    send_read_receipts   BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_at           DATETIME(6) NOT NULL,
    PRIMARY KEY (user_id),
    CONSTRAINT fk_user_settings_user_id FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE group_invite_allowlist (
    user_id          CHAR(36)    CHARACTER SET ascii NOT NULL,
    allowed_user_id  CHAR(36)    CHARACTER SET ascii NOT NULL,
    created_at       DATETIME(6) NOT NULL,
    PRIMARY KEY (user_id, allowed_user_id),
    CONSTRAINT fk_group_invite_allowlist_user_id FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_group_invite_allowlist_allowed_user_id FOREIGN KEY (allowed_user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE blocklist (
    user_id          CHAR(36)    CHARACTER SET ascii NOT NULL,
    blocked_user_id  CHAR(36)    CHARACTER SET ascii NOT NULL,
    created_at       DATETIME(6) NOT NULL,
    PRIMARY KEY (user_id, blocked_user_id),
    CONSTRAINT fk_blocklist_user_id FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_blocklist_blocked_user_id FOREIGN KEY (blocked_user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE admin_users (
    id              INT          NOT NULL AUTO_INCREMENT,
    username        VARCHAR(64)  NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    failed_logins   INT          NOT NULL DEFAULT 0,
    locked_until    DATETIME(6)  NULL,
    last_login_at   DATETIME(6)  NULL,
    created_at      DATETIME(6)  NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_admin_users_username (username)
) ENGINE=InnoDB;

CREATE TABLE admin_audit_log (
    id           BIGINT      NOT NULL AUTO_INCREMENT,
    admin_id     INT         NULL,
    action       VARCHAR(64) NOT NULL,
    target_type  VARCHAR(32) NOT NULL DEFAULT '',
    target_id    VARCHAR(64) NOT NULL DEFAULT '',
    details      TEXT        NOT NULL,
    ip_address   VARCHAR(45) CHARACTER SET ascii NOT NULL DEFAULT '',
    created_at   DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    KEY ix_admin_audit_log_created_at (created_at),
    CONSTRAINT fk_admin_audit_log_admin_id FOREIGN KEY (admin_id) REFERENCES admin_users (id)
) ENGINE=InnoDB;

CREATE TABLE reports (
    id               CHAR(36)    CHARACTER SET ascii NOT NULL,
    reporter_id      CHAR(36)    CHARACTER SET ascii NOT NULL,
    target_user_id   CHAR(36)    CHARACTER SET ascii NULL,
    thread_id        CHAR(36)    CHARACTER SET ascii NULL,
    message_id       CHAR(36)    CHARACTER SET ascii NULL,
    reason           ENUM('spam','abuse','illegal','other') NOT NULL,
    details          TEXT        NOT NULL,
    status           ENUM('open','in_review','resolved','rejected') NOT NULL DEFAULT 'open',
    resolution_note  TEXT        NOT NULL,
    handled_by       INT         NULL,
    created_at       DATETIME(6) NOT NULL,
    updated_at       DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    KEY ix_reports_status (status),
    CONSTRAINT fk_reports_reporter_id FOREIGN KEY (reporter_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_reports_handled_by FOREIGN KEY (handled_by) REFERENCES admin_users (id)
) ENGINE=InnoDB;
