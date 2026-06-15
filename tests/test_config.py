import textwrap
import pytest
from formfiller.config import load_config, load_profile, ProfileField, AppConfig


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def test_load_config_reads_threshold_and_paths(tmp_path):
    cfg_file = _write(tmp_path, "config.yaml", """
        confidence_threshold: 0.8
        dry_run: false
        excel_log_path: C:/synced/log.xlsx
        review_queue_dir: ./review_queue
        inbox_list_count: 25
        azure_openai_deployment: gpt-4o
        azure_api_version: "2024-10-21"
    """)
    cfg = load_config(cfg_file)
    assert cfg.confidence_threshold == 0.8
    assert cfg.dry_run is False
    assert cfg.inbox_list_count == 25
    assert cfg.azure_openai_deployment == "gpt-4o"
    assert cfg.azure_api_version == "2024-10-21"


def test_load_profile_parses_fields_with_aliases(tmp_path):
    prof_file = _write(tmp_path, "profile.yaml", """
        fields:
          - name: company_legal_name
            value: Ginesis Finance SAS
            aliases: ["company name", "raison sociale"]
          - name: vat_number
            value: FR12345678901
            aliases: ["VAT", "N° TVA", "tax id"]
    """)
    profile = load_profile(prof_file)
    assert len(profile) == 2
    vat = next(f for f in profile if f.name == "vat_number")
    assert vat.value == "FR12345678901"
    assert "VAT" in vat.aliases


def test_profile_field_is_frozen():
    f = ProfileField(name="x", value="y", aliases=())
    with pytest.raises(Exception):
        f.value = "z"


from formfiller.config import azure_v1_base_url


def test_azure_v1_base_url_from_bare_host():
    assert azure_v1_base_url("https://r.services.ai.azure.com") == "https://r.services.ai.azure.com/openai/v1/"


def test_azure_v1_base_url_repairs_typo_and_strips_path():
    # doubled-h typo + a full /openai/v1/responses path both get normalized away
    assert (
        azure_v1_base_url("hhttps://r.services.ai.azure.com/openai/v1/responses")
        == "https://r.services.ai.azure.com/openai/v1/"
    )


def test_azure_v1_base_url_adds_missing_scheme():
    assert azure_v1_base_url("r.openai.azure.com/") == "https://r.openai.azure.com/openai/v1/"


def test_appconfig_agent_defaults():
    cfg = AppConfig(excel_log_path="x.xlsx")
    assert cfg.fill_strategy == "deterministic"
    assert cfg.agent_model_deployment == ""   # falls back to azure_openai_deployment when blank
    assert cfg.max_steps == 20
    assert cfg.no_progress_limit == 5
    assert cfg.traces_dir == "./traces"


def test_appconfig_agent_overrides():
    cfg = AppConfig(
        excel_log_path="x.xlsx", fill_strategy="agent",
        agent_model_deployment="gpt-5.4", max_steps=12,
        no_progress_limit=3, traces_dir="./t",
    )
    assert cfg.fill_strategy == "agent"
    assert cfg.agent_model_deployment == "gpt-5.4"
    assert cfg.max_steps == 12
    assert cfg.no_progress_limit == 3
    assert cfg.traces_dir == "./t"


def test_appconfig_rejects_unknown_fill_strategy():
    import pytest
    from pydantic import ValidationError
    from formfiller.config import AppConfig
    with pytest.raises(ValidationError):
        AppConfig(excel_log_path="x.xlsx", fill_strategy="llm")
