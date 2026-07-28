from jfvb.answers import Answers, DatabaseMode
from jfvb.rules import database


def test_bundled_mode_enables_bundled_postgres_and_warns():
    answers = Answers()
    answers.database.mode = DatabaseMode.BUNDLED
    result = database.apply(answers)
    assert result.values["postgresql"]["enabled"] is True
    assert any("WARNING" in note for note in result.notes)


def test_external_mode_disables_bundled_and_sets_global_host():
    answers = Answers()
    answers.database.mode = DatabaseMode.EXTERNAL
    answers.database.shared_host = "pg.acme.com"
    result = database.apply(answers)
    assert result.values["postgresql"]["enabled"] is False
    assert result.values["global"]["database"]["host"] == "pg.acme.com"


def test_external_mode_creates_per_product_secret_refs():
    answers = Answers()
    answers.database.mode = DatabaseMode.EXTERNAL
    answers.database.shared_host = "pg.acme.com"
    answers.products.distribution = True
    result = database.apply(answers)
    for product in ("artifactory", "xray", "distribution"):
        assert result.values[product]["database"]["user"] == product
        assert "secrets" in result.values[product]["database"]
    assert "catalog" not in result.values


def test_per_product_host_override_generates_explicit_url():
    answers = Answers()
    answers.database.mode = DatabaseMode.EXTERNAL
    answers.database.shared_host = "pg.acme.com"
    answers.database.per_product_host = {"xray": "xray-pg.acme.com"}
    result = database.apply(answers)
    assert "xray-pg.acme.com" in result.values["xray"]["database"]["url"]
    assert result.values["xray"]["database"]["url"].startswith("postgres://")
    assert "url" not in result.values["artifactory"]["database"]
    # artifactory still falls back to the shared host, so the global block
    # is still needed here.
    assert result.values["global"]["database"]["host"] == "pg.acme.com"


def test_global_database_host_omitted_but_initdbcreation_kept_when_every_product_has_explicit_host():
    # Only artifactory + xray enabled (defaults), and both get an explicit
    # per-product host -- global.database.host would never be consulted,
    # so it shouldn't be written. But initDBCreation: false is required by
    # the chart whenever bundled postgres is disabled (confirmed via live
    # `helm template` -- the chart hard-fails without it), independent of
    # whether the host fallback is used, so it must still be present.
    answers = Answers()
    answers.database.mode = DatabaseMode.EXTERNAL
    answers.products.xray = True
    answers.database.per_product_host = {
        "artifactory": "art-pg.acme.com",
        "xray": "xray-pg.acme.com",
    }
    result = database.apply(answers)
    assert result.values["global"]["database"] == {"initDBCreation": False}
    assert any("omitted" in note for note in result.notes)


def test_global_database_initdbcreation_always_false_for_external_mode():
    for per_product_host in ({}, {"artifactory": "art.acme.com", "xray": "xray.acme.com"}):
        answers = Answers()
        answers.database.mode = DatabaseMode.EXTERNAL
        answers.products.xray = True
        answers.database.shared_host = "pg.acme.com" if not per_product_host else None
        answers.database.per_product_host = per_product_host
        result = database.apply(answers)
        assert result.values["global"]["database"]["initDBCreation"] is False


def test_no_placeholder_host_in_connectivity_check_when_all_hosts_explicit():
    answers = Answers()
    answers.database.mode = DatabaseMode.EXTERNAL
    answers.products.xray = True
    answers.database.per_product_host = {
        "artifactory": "art-pg.acme.com",
        "xray": "xray-pg.acme.com",
    }
    result = database.apply(answers)
    check_step = next(s for s in result.steps if "reachability" in s.title)
    joined = "\n".join(check_step.commands)
    assert "<DB_HOST>" not in joined
    assert "art-pg.acme.com" in joined
    assert "xray-pg.acme.com" in joined


def test_warning_scoped_to_products_actually_missing_a_host():
    answers = Answers()
    answers.database.mode = DatabaseMode.EXTERNAL
    answers.products.xray = True
    answers.database.per_product_host = {"xray": "xray-pg.acme.com"}
    result = database.apply(answers)
    warnings = [n for n in result.notes if n.startswith("WARNING")]
    assert len(warnings) == 1
    assert "artifactory" in warnings[0]
    assert "xray" not in warnings[0]


def test_sql_generation_uses_product_specific_names():
    sql = database._sql_for_product("xray")
    assert "CREATE DATABASE xray;" in sql
    assert "CREATE USER xray" in sql
