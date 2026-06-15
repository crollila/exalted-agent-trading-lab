"""Team-aware paper credential resolution and auth diagnostics.

The lab supports three independent Alpaca paper credential sources:

* ``global``      -> ALPACA_API_KEY / ALPACA_SECRET_KEY / ALPACA_PAPER / ALPACA_BASE_URL
* ``team_alpha``  -> TEAM_ALPHA_ALPACA_* (see :mod:`src.brokers.team_alpaca_config`)
* ``team_beta``   -> TEAM_BETA_ALPACA_*

Global credentials may be invalid without blocking the teams. Team execution
never falls back to global keys — each team uses only its own credentials.

Nothing here ever prints secret values; only presence and length are exposed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from src.brokers.alpaca_client import PAPER_BASE_URL, AlpacaClientWrapper
from src.brokers.team_alpaca_config import TeamAlpacaPaperConfig
from src.config.settings import Settings

CREDENTIAL_SOURCES = ("global", "team_alpha", "team_beta")
TEAM_SOURCES = ("team_alpha", "team_beta")

# Failure classifications.
OK = "ok"
MISSING_ENV = "missing_env"
ENDPOINT_MISMATCH = "endpoint_mismatch"
UNAUTHORIZED_401 = "unauthorized_401"
FORBIDDEN_403 = "forbidden_403"
NETWORK_ERROR = "network_error"
SDK_ERROR = "sdk_error"
UNKNOWN = "unknown"

GLOBAL_ENV_NAMES = {
    "api_key": "ALPACA_API_KEY",
    "secret_key": "ALPACA_SECRET_KEY",
    "paper": "ALPACA_PAPER",
    "base_url": "ALPACA_BASE_URL",
}


@dataclass(frozen=True)
class CredentialRef:
    source: str
    api_key: str | None
    secret_key: str | None
    paper: bool | None
    base_url: str | None
    env_names: dict[str, str]


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def resolve_credentials(source: str, env: Mapping[str, str] | None = None) -> CredentialRef:
    if source not in CREDENTIAL_SOURCES:
        raise ValueError(f"Unknown credential source: {source}. Use one of {CREDENTIAL_SOURCES}.")
    if env is None:
        env = os.environ

    if source == "global":
        paper_raw = _clean(env.get(GLOBAL_ENV_NAMES["paper"]))
        return CredentialRef(
            source="global",
            api_key=_clean(env.get(GLOBAL_ENV_NAMES["api_key"])),
            secret_key=_clean(env.get(GLOBAL_ENV_NAMES["secret_key"])),
            paper=None if paper_raw is None else paper_raw.lower() == "true",
            base_url=_clean(env.get(GLOBAL_ENV_NAMES["base_url"])),
            env_names=dict(GLOBAL_ENV_NAMES),
        )

    team_cfg = TeamAlpacaPaperConfig.from_env(source, env=env)
    prefix = team_cfg.env_prefix
    return CredentialRef(
        source=source,
        api_key=team_cfg.api_key,
        secret_key=team_cfg.secret_key,
        paper=team_cfg.paper,
        base_url=team_cfg.base_url,
        env_names={
            "api_key": f"{prefix}_ALPACA_API_KEY",
            "secret_key": f"{prefix}_ALPACA_SECRET_KEY",
            "paper": f"{prefix}_ALPACA_PAPER",
            "base_url": f"{prefix}_ALPACA_BASE_URL",
        },
    )


def settings_for_source(
    source: str,
    base_settings: Settings | None = None,
    env: Mapping[str, str] | None = None,
) -> Settings:
    """Build a Settings object using a specific credential source.

    Non-credential fields (db path, equity, caps) come from ``base_settings``.
    """

    base = base_settings or Settings.from_env()
    ref = resolve_credentials(source, env)
    return Settings(
        alpaca_api_key=ref.api_key,
        alpaca_secret_key=ref.secret_key,
        alpaca_paper=ref.paper,
        alpaca_base_url=ref.base_url or "",
        database_path=base.database_path,
        dry_run=base.dry_run,
        starting_equity=base.starting_equity,
        min_cash_pct=base.min_cash_pct,
        max_position_pct=base.max_position_pct,
        max_daily_turnover_pct=base.max_daily_turnover_pct,
        max_new_positions_per_day=base.max_new_positions_per_day,
    )


def client_for_source(
    source: str,
    *,
    base_settings: Settings | None = None,
    env: Mapping[str, str] | None = None,
    client_factory: Callable[[Settings], Any] | None = None,
    kill_switch_path: str | None = None,
    options_adapter: Any | None = None,
) -> AlpacaClientWrapper:
    """Build an AlpacaClientWrapper bound to exactly one credential source.

    Team sources never fall back to global keys. Raises ValueError (from the
    wrapper) if the source is not paper-mode on the exact paper endpoint.
    """

    settings = settings_for_source(source, base_settings, env)
    return AlpacaClientWrapper(
        settings=settings,
        client_factory=client_factory,
        kill_switch_path=kill_switch_path,
        options_adapter=options_adapter,
    )


def _read_value(obj: object, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


@dataclass(frozen=True)
class AuthDiagnosis:
    source: str
    api_key_present: bool
    api_key_length: int
    secret_present: bool
    secret_length: int
    paper_valid: bool
    base_url_valid: bool
    auth_ok: bool
    classification: str
    message: str
    account: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "api_key_present": self.api_key_present,
            "api_key_length": self.api_key_length,
            "secret_present": self.secret_present,
            "secret_length": self.secret_length,
            "paper_valid": self.paper_valid,
            "base_url_valid": self.base_url_valid,
            "auth_ok": self.auth_ok,
            "classification": self.classification,
            "message": self.message,
            "account": self.account,
        }


def _classify_exception(exc: Exception) -> tuple[str, str]:
    # Try Alpaca APIError status code first.
    status = getattr(exc, "status_code", None)
    if status == 401:
        return UNAUTHORIZED_401, "unauthorized (401)"
    if status == 403:
        return FORBIDDEN_403, "forbidden (403)"

    name = type(exc).__name__
    if name in ("ConnectionError", "Timeout", "ConnectTimeout", "ReadTimeout"):
        return NETWORK_ERROR, f"network error: {name}"

    text = str(exc)
    if "401" in text or "unauthorized" in text.lower():
        return UNAUTHORIZED_401, "unauthorized (401)"
    if "403" in text or "forbidden" in text.lower():
        return FORBIDDEN_403, "forbidden (403)"
    if name == "APIError":
        return SDK_ERROR, f"SDK error: {text}"
    return UNKNOWN, f"{name}: {text}"


def diagnose_source(
    source: str,
    *,
    base_settings: Settings | None = None,
    env: Mapping[str, str] | None = None,
    client_factory: Callable[[Settings], Any] | None = None,
    attempt_auth: bool = True,
) -> AuthDiagnosis:
    """Diagnose one credential source without ever exposing secret values."""

    ref = resolve_credentials(source, env)
    api_present = bool(ref.api_key)
    secret_present = bool(ref.secret_key)
    api_len = len(ref.api_key) if ref.api_key else 0
    secret_len = len(ref.secret_key) if ref.secret_key else 0
    paper_valid = ref.paper is True
    base_url_valid = ref.base_url == PAPER_BASE_URL

    def build(classification: str, message: str, auth_ok: bool, account=None) -> AuthDiagnosis:
        return AuthDiagnosis(
            source=source,
            api_key_present=api_present,
            api_key_length=api_len,
            secret_present=secret_present,
            secret_length=secret_len,
            paper_valid=paper_valid,
            base_url_valid=base_url_valid,
            auth_ok=auth_ok,
            classification=classification,
            message=message,
            account=account,
        )

    if not (api_present and secret_present):
        missing = [
            ref.env_names["api_key"] if not api_present else None,
            ref.env_names["secret_key"] if not secret_present else None,
        ]
        missing = [name for name in missing if name]
        return build(MISSING_ENV, f"missing credentials: {', '.join(missing)}", auth_ok=False)

    if not paper_valid:
        return build(
            ENDPOINT_MISMATCH,
            f"{ref.env_names['paper']} must be true for paper-only mode",
            auth_ok=False,
        )
    if not base_url_valid:
        return build(
            ENDPOINT_MISMATCH,
            f"{ref.env_names['base_url']} must be exactly {PAPER_BASE_URL}",
            auth_ok=False,
        )

    if not attempt_auth:
        return build(UNKNOWN, "auth not attempted", auth_ok=False)

    try:
        settings = settings_for_source(source, base_settings, env)
        client = AlpacaClientWrapper(settings=settings, client_factory=client_factory)
        account = client.get_account()
        snapshot = {
            "equity": _read_value(account, "equity"),
            "cash": _read_value(account, "cash"),
            "buying_power": _read_value(account, "buying_power"),
        }
        return build(OK, "authenticated", auth_ok=True, account=snapshot)
    except ValueError as exc:
        # Wrapper-level safety refusal (paper/base url) — treat as endpoint mismatch.
        return build(ENDPOINT_MISMATCH, str(exc), auth_ok=False)
    except RuntimeError as exc:
        return build(MISSING_ENV, str(exc), auth_ok=False)
    except Exception as exc:  # noqa: BLE001 - classified below; secrets never included
        classification, message = _classify_exception(exc)
        return build(classification, message, auth_ok=False)


def diagnose_all(
    *,
    base_settings: Settings | None = None,
    env: Mapping[str, str] | None = None,
    client_factory: Callable[[Settings], Any] | None = None,
    attempt_auth: bool = True,
) -> dict[str, AuthDiagnosis]:
    return {
        source: diagnose_source(
            source,
            base_settings=base_settings,
            env=env,
            client_factory=client_factory,
            attempt_auth=attempt_auth,
        )
        for source in CREDENTIAL_SOURCES
    }
