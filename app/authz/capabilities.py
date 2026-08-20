"""Capability constants and the default role-to-capability map.

Default deny: a capability is granted only when an effective role assignment lists it
and its scope covers the request. This starter map covers Phase 1 foundation needs and
is expanded to the full matrix (ADR-005A) in later phases.
"""

from __future__ import annotations

# Technical / super-admin
TECHNICAL_CONFIG = "technical_config"
RESET_USER_AUTH = "reset_user_auth"
VIEW_AUDIT = "view_audit"
CREATE_MANAGER = "create_manager"

# Workforce administration
MANAGE_ROLES = "manage_roles"
APPOINT_TEAM_LEADER = "appoint_team_leader"
APPOINT_TEAM_CAPTAIN = "appoint_team_captain"
CREATE_AGENT = "create_agent"

# Campaigns and imports (plan 6.3; Phase 2 scope)
CREATE_CAMPAIGN = "create_campaign"
VIEW_CAMPAIGN = "view_campaign"
MANAGE_CAMPAIGN = "manage_campaign"  # edit draft, manage imports, dispositions
PAUSE_CAMPAIGN = "pause_campaign"
LAUNCH_CAMPAIGN = "launch_campaign"
ARCHIVE_CAMPAIGN = "archive_campaign"

# Agent work queue (Phase 3)
WORK_QUEUE = "work_queue"

ROLE_SUPER_ADMIN = "super_admin"
ROLE_MANAGER = "manager"
ROLE_TEAM_LEADER = "team_leader"
ROLE_TEAM_CAPTAIN = "team_captain"
ROLE_AGENT = "agent"
ROLE_VIEWER = "viewer"

ROLE_CAPABILITIES: dict[str, set[str]] = {
    ROLE_SUPER_ADMIN: {TECHNICAL_CONFIG, RESET_USER_AUTH, VIEW_AUDIT, CREATE_MANAGER, MANAGE_ROLES},
    ROLE_MANAGER: {
        APPOINT_TEAM_LEADER,
        APPOINT_TEAM_CAPTAIN,
        CREATE_AGENT,
        MANAGE_ROLES,
        CREATE_CAMPAIGN,
        VIEW_CAMPAIGN,
        MANAGE_CAMPAIGN,
        PAUSE_CAMPAIGN,
        LAUNCH_CAMPAIGN,
        ARCHIVE_CAMPAIGN,
    },
    ROLE_TEAM_LEADER: {
        APPOINT_TEAM_CAPTAIN,
        CREATE_AGENT,
        CREATE_CAMPAIGN,
        VIEW_CAMPAIGN,
        MANAGE_CAMPAIGN,
        PAUSE_CAMPAIGN,
    },
    ROLE_TEAM_CAPTAIN: {CREATE_AGENT, VIEW_CAMPAIGN},
    ROLE_AGENT: {WORK_QUEUE},
    ROLE_VIEWER: set(),
}
