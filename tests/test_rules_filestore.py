from jfvb.answers import Answers, FilestoreMode, FilestoreType, SizeTier
from jfvb.rules import filestore


def test_local_filestore_single_node_produces_no_persistence_patch():
    answers = Answers()
    answers.filestore.type = FilestoreType.LOCAL
    answers.size_tier = SizeTier.CUSTOM
    answers.custom_sizing["artifactory_replicas"] = 1
    result = filestore.apply(answers)
    assert result.values == {}


def test_local_filestore_ha_switches_to_cluster_file_system():
    answers = Answers()
    answers.filestore.type = FilestoreType.LOCAL
    answers.size_tier = SizeTier.LARGE
    result = filestore.apply(answers)
    persistence = result.values["artifactory"]["artifactory"]["persistence"]
    assert persistence["type"] == "cluster-file-system"
    assert any("cluster-file-system" in note for note in result.notes)


def test_s3_filestore_only_touches_artifactory():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.s3_bucket = "my-bucket"
    answers.filestore.s3_region = "us-east-1"
    result = filestore.apply(answers)
    assert list(result.values.keys()) == ["artifactory"]
    persistence = result.values["artifactory"]["artifactory"]["persistence"]
    assert persistence["type"] == "s3-storage-v3-direct"
    assert persistence["awsS3V3"]["bucketName"] == "my-bucket"


def test_never_touches_xray_distribution_or_catalog():
    for ftype in FilestoreType:
        answers = Answers()
        answers.filestore.type = ftype
        result = filestore.apply(answers)
        assert "xray" not in result.values
        assert "distribution" not in result.values
        assert "catalog" not in result.values


def test_nfs_sets_ip_and_capacity():
    answers = Answers()
    answers.filestore.type = FilestoreType.NFS
    answers.filestore.nfs_ip = "10.0.0.5"
    result = filestore.apply(answers)
    assert result.values["artifactory"]["artifactory"]["persistence"]["nfs"]["ip"] == "10.0.0.5"


def test_custom_filestore_produces_warning_note_not_values():
    answers = Answers()
    answers.filestore.type = FilestoreType.CUSTOM
    result = filestore.apply(answers)
    assert result.values == {}
    assert any("custom" in note.lower() for note in result.notes)


def test_custom_filestore_notes_mention_sharding():
    answers = Answers()
    answers.filestore.type = FilestoreType.CUSTOM
    result = filestore.apply(answers)
    assert any("sharding" in note.lower() for note in result.notes)


def test_s3_eventual_mode_uses_aws_s3_v3_persistence_type():
    # NOT "s3-storage-v3" -- that string is Artifactory's raw binary
    # provider template name, but the chart's persistence.type enum (and
    # its binarystore.xml template conditionals) use "aws-s3-v3" for the
    # eventual chain instead.
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.mode = FilestoreMode.EVENTUAL
    result = filestore.apply(answers)
    persistence = result.values["artifactory"]["artifactory"]["persistence"]
    assert persistence["type"] == "aws-s3-v3"


def test_s3_cluster_mode_uses_cluster_persistence_type():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.mode = FilestoreMode.CLUSTER
    result = filestore.apply(answers)
    persistence = result.values["artifactory"]["artifactory"]["persistence"]
    assert persistence["type"] == "cluster-s3-storage-v3"


def test_s3_archive_mode_uses_archive_persistence_type_and_warns_low_confidence():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.mode = FilestoreMode.ARCHIVE
    result = filestore.apply(answers)
    persistence = result.values["artifactory"]["artifactory"]["persistence"]
    assert persistence["type"] == "s3-storage-v3-archive"
    assert any("low confidence" in note.lower() for note in result.notes)


def test_gcs_mode_variants_map_to_correct_persistence_types():
    expected = {
        FilestoreMode.DIRECT: "google-storage-v2-direct",
        FilestoreMode.EVENTUAL: "google-storage-v2",
        FilestoreMode.CLUSTER: "cluster-google-storage-v2",
    }
    for mode, persistence_type in expected.items():
        answers = Answers()
        answers.filestore.type = FilestoreType.GCS
        answers.filestore.mode = mode
        result = filestore.apply(answers)
        assert (
            result.values["artifactory"]["artifactory"]["persistence"]["type"]
            == persistence_type
        )


def test_azure_blob_mode_variants_map_to_correct_persistence_types():
    # Only Direct gets the V2 binary provider -- the chart's binarystore.xml
    # has no V2 eventual/cluster branch, so those fall back to V1 type
    # strings ("azure-blob" / "cluster-azure-blob-storage").
    expected = {
        FilestoreMode.DIRECT: "azure-blob-storage-v2-direct",
        FilestoreMode.EVENTUAL: "azure-blob",
        FilestoreMode.CLUSTER: "cluster-azure-blob-storage",
    }
    for mode, persistence_type in expected.items():
        answers = Answers()
        answers.filestore.type = FilestoreType.AZURE_BLOB
        answers.filestore.mode = mode
        result = filestore.apply(answers)
        assert (
            result.values["artifactory"]["artifactory"]["persistence"]["type"]
            == persistence_type
        )


def test_azure_blob_eventual_notes_mention_v1_fallback():
    answers = Answers()
    answers.filestore.type = FilestoreType.AZURE_BLOB
    answers.filestore.mode = FilestoreMode.EVENTUAL
    result = filestore.apply(answers)
    assert any("v1" in note.lower() for note in result.notes)


def test_eventual_mode_warns_when_ha_and_not_when_single_node():
    ha_answers = Answers()
    ha_answers.filestore.type = FilestoreType.S3
    ha_answers.filestore.mode = FilestoreMode.EVENTUAL
    ha_answers.size_tier = SizeTier.LARGE
    ha_result = filestore.apply(ha_answers)
    assert any("shared mount" in note.lower() for note in ha_result.notes)

    single_answers = Answers()
    single_answers.filestore.type = FilestoreType.S3
    single_answers.filestore.mode = FilestoreMode.EVENTUAL
    single_answers.size_tier = SizeTier.CUSTOM
    single_answers.custom_sizing["artifactory_replicas"] = 1
    single_result = filestore.apply(single_answers)
    assert not any("shared mount" in note.lower() for note in single_result.notes)


def test_cluster_mode_warns_when_single_node():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.mode = FilestoreMode.CLUSTER
    answers.size_tier = SizeTier.CUSTOM
    answers.custom_sizing["artifactory_replicas"] = 1
    result = filestore.apply(answers)
    assert any("no benefit" in note.lower() for note in result.notes)


def test_full_db_with_cache_generates_full_db_template_and_secret_step():
    answers = Answers()
    answers.filestore.type = FilestoreType.FULL_DB
    answers.filestore.full_db_use_cache = True
    result = filestore.apply(answers)
    persistence = result.values["artifactory"]["artifactory"]["persistence"]
    assert persistence["customBinarystoreXmlSecret"] == "artifactory-full-db-binarystore"
    assert len(result.steps) == 1
    xml_text = "\n".join(result.steps[0].commands)
    assert 'template="full-db"' in xml_text
    assert '<provider id="cache-fs" type="cache-fs">' in xml_text


def test_direct_cloud_download_sets_fields_on_s3():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.direct_cloud_download = True
    answers.filestore.signed_url_expiry_seconds = 45
    result = filestore.apply(answers)
    aws_s3 = result.values["artifactory"]["artifactory"]["persistence"]["awsS3V3"]
    assert aws_s3["enableSignedUrlRedirect"] is True
    assert aws_s3["signedUrlExpirySeconds"] == 45
    assert any("direct cloud storage download" in n.lower() for n in result.notes)
    assert any("downloadRedirect" in " ".join(s.commands) for s in result.steps)


def test_direct_cloud_download_sets_fields_on_gcs():
    answers = Answers()
    answers.filestore.type = FilestoreType.GCS
    answers.filestore.direct_cloud_download = True
    result = filestore.apply(answers)
    gs = result.values["artifactory"]["artifactory"]["persistence"]["googleStorage"]
    assert gs["enableSignedUrlRedirect"] is True


def test_direct_cloud_download_off_by_default():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    result = filestore.apply(answers)
    aws_s3 = result.values["artifactory"]["artifactory"]["persistence"]["awsS3V3"]
    assert "enableSignedUrlRedirect" not in aws_s3


def test_local_cache_toggle_enables_cache_fs_for_single_node():
    answers = Answers()
    answers.filestore.type = FilestoreType.LOCAL
    answers.filestore.local_cache_enabled = True
    answers.size_tier = SizeTier.CUSTOM
    answers.custom_sizing["artifactory_replicas"] = 1
    result = filestore.apply(answers)
    assert (
        result.values["artifactory"]["artifactory"]["persistence"]["fileSystem"]["cache"]["enabled"]
        is True
    )


def test_local_cache_toggle_ignored_when_ha_since_cluster_fs_used_instead():
    answers = Answers()
    answers.filestore.type = FilestoreType.LOCAL
    answers.filestore.local_cache_enabled = True
    answers.size_tier = SizeTier.LARGE
    result = filestore.apply(answers)
    persistence = result.values["artifactory"]["artifactory"]["persistence"]
    assert persistence["type"] == "cluster-file-system"
    assert "fileSystem" not in persistence


def test_migration_note_appears_for_non_fresh_deployment_switching_to_s3():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.fresh_deployment = False
    result = filestore.apply(answers)
    assert any("migration" in n.lower() for n in result.notes)


def test_no_migration_note_for_fresh_deployment():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.fresh_deployment = True
    result = filestore.apply(answers)
    assert not any("migration" in n.lower() for n in result.notes)


def test_full_db_direct_generates_full_db_direct_template():
    answers = Answers()
    answers.filestore.type = FilestoreType.FULL_DB
    answers.filestore.full_db_use_cache = False
    result = filestore.apply(answers)
    xml_text = "\n".join(result.steps[0].commands)
    assert 'template="full-db-direct"' in xml_text
    assert "cache-fs" not in xml_text
    assert any("warning" in note.lower() for note in result.notes)


def test_s3_compatible_non_aws_enables_path_style_access():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.s3_compatible_non_aws = True
    answers.filestore.s3_endpoint = "https://minio.internal:9000"
    result = filestore.apply(answers)
    aws_s3 = result.values["artifactory"]["artifactory"]["persistence"]["awsS3V3"]
    assert aws_s3["enablePathStyleAccess"] is True
    assert aws_s3["useInstanceCredentials"] is False
    assert any("path-style" in n.lower() or "path style" in n.lower() for n in result.notes)


def test_s3_compatible_non_aws_sets_http_and_port_when_requested():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.s3_compatible_non_aws = True
    answers.filestore.s3_endpoint = "http://10.0.0.5"
    answers.filestore.s3_use_http = True
    answers.filestore.s3_port = 9000
    result = filestore.apply(answers)
    aws_s3 = result.values["artifactory"]["artifactory"]["persistence"]["awsS3V3"]
    assert aws_s3["useHttp"] is True
    assert aws_s3["port"] == 9000


def test_s3_compatible_non_aws_without_http_omits_use_http_and_port():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.s3_compatible_non_aws = True
    answers.filestore.s3_endpoint = "https://minio.internal"
    result = filestore.apply(answers)
    aws_s3 = result.values["artifactory"]["artifactory"]["persistence"]["awsS3V3"]
    assert "useHttp" not in aws_s3
    assert "port" not in aws_s3


def test_s3_compatible_non_aws_warns_when_endpoint_missing():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.s3_compatible_non_aws = True
    result = filestore.apply(answers)
    assert any("no endpoint was provided" in n.lower() for n in result.notes)


def test_s3_compatible_non_aws_warns_about_licensing_and_kms_cloudfront():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.s3_compatible_non_aws = True
    result = filestore.apply(answers)
    assert any("enterprise" in n.lower() for n in result.notes)
    assert any("kms" in n.lower() and "cloudfront" in n.lower() for n in result.notes)


def test_s3_compatible_non_aws_iam_request_is_overridden_to_static_creds():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.s3_compatible_non_aws = True
    answers.filestore.s3_use_iam_role = True  # doesn't apply outside AWS
    result = filestore.apply(answers)
    aws_s3 = result.values["artifactory"]["artifactory"]["persistence"]["awsS3V3"]
    assert aws_s3["useInstanceCredentials"] is False
    assert any("instance-metadata" in n.lower() for n in result.notes)


def test_s3_compatible_non_aws_direct_download_gets_unverified_warning():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.s3_compatible_non_aws = True
    answers.filestore.direct_cloud_download = True
    result = filestore.apply(answers)
    assert any("unverified" in n.lower() for n in result.notes)


def test_s3_static_credentials_sets_use_instance_credentials_false_and_out_of_band_step():
    # Real gap fix: previously choosing static keys never actually disabled
    # the chart's useInstanceCredentials default (true), so Artifactory
    # would still try (and fail) the AWS IAM chain instead of using the
    # provided keys.
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.s3_use_iam_role = False
    result = filestore.apply(answers)
    aws_s3 = result.values["artifactory"]["artifactory"]["persistence"]["awsS3V3"]
    assert aws_s3["useInstanceCredentials"] is False
    assert "identity" not in aws_s3
    assert "credential" not in aws_s3
    step_texts = " ".join(" ".join(s.commands) for s in result.steps)
    assert "--set-string" in step_texts
    assert "awsS3V3.identity" in step_texts
    assert "awsS3V3.credential" in step_texts


def test_s3_iam_role_true_omits_static_credential_step():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.s3_use_iam_role = True
    result = filestore.apply(answers)
    aws_s3 = result.values["artifactory"]["artifactory"]["persistence"]["awsS3V3"]
    assert aws_s3["useInstanceCredentials"] is True
    step_texts = " ".join(" ".join(s.commands) for s in result.steps)
    assert "--set-string" not in step_texts


def test_azure_account_key_never_written_to_values_and_uses_out_of_band_step():
    answers = Answers()
    answers.filestore.type = FilestoreType.AZURE_BLOB
    answers.filestore.azure_account_name = "myaccount"
    result = filestore.apply(answers)
    azure_blob = result.values["artifactory"]["artifactory"]["persistence"]["azureBlob"]
    assert "accountKey" not in azure_blob
    step_texts = " ".join(" ".join(s.commands) for s in result.steps)
    assert "--set-string" in step_texts
    assert "azureBlob.accountKey" in step_texts


def test_s3_static_credentials_note_is_a_warning_surfaced_in_summary():
    # Choosing identity/credential over IAM is a security-relevant decision
    # that still needs a real follow-up action (supplying the actual key at
    # install time) -- it must show up in the top-level Warnings section
    # with a pointer to the runbook step, not get buried as an FYI note.
    from jfvb.builder import Contribution
    from jfvb.render import _runbook_reference

    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.s3_use_iam_role = False
    result = filestore.apply(answers)
    warning_notes = [n for n in result.notes if n.startswith("WARNING")]
    assert any("identity/credential" in n.lower() for n in warning_notes)
    assert any("--set-string" in " ".join(s.commands) for s in result.steps)

    contribution = Contribution(
        rule_name="filestore", keys=list(result.values), notes=result.notes, steps=result.steps
    )
    assert _runbook_reference(contribution)  # points at the RUNBOOK.md fix-it step


def test_s3_compatible_iam_override_note_is_a_warning():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.s3_compatible_non_aws = True
    answers.filestore.s3_use_iam_role = True
    result = filestore.apply(answers)
    warning_notes = [n for n in result.notes if n.startswith("WARNING")]
    assert any("instance-metadata" in n.lower() for n in warning_notes)


def test_azure_account_key_note_is_a_warning():
    answers = Answers()
    answers.filestore.type = FilestoreType.AZURE_BLOB
    result = filestore.apply(answers)
    warning_notes = [n for n in result.notes if n.startswith("WARNING")]
    assert any("account key" in n.lower() for n in warning_notes)


def test_s3_dotted_bucket_name_forces_path_style_even_on_aws():
    # Real AWS gotcha, not just non-AWS: a dotted bucket name breaks TLS
    # hostname verification under virtual-hosted-style addressing because
    # AWS's wildcard cert only covers one subdomain level.
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.s3_bucket = "my.dotted.bucket"
    result = filestore.apply(answers)
    aws_s3 = result.values["artifactory"]["artifactory"]["persistence"]["awsS3V3"]
    assert aws_s3["enablePathStyleAccess"] is True
    assert any(
        "contains dots" in n.lower() and "warning" in n.lower() for n in result.notes
    )


def test_s3_non_dotted_bucket_name_on_aws_omits_path_style():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.s3_bucket = "my-plain-bucket"
    result = filestore.apply(answers)
    aws_s3 = result.values["artifactory"]["artifactory"]["persistence"]["awsS3V3"]
    assert "enablePathStyleAccess" not in aws_s3


def test_s3_uppercase_bucket_name_warns_about_invalid_naming():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.s3_bucket = "My-Bucket"
    result = filestore.apply(answers)
    warning_notes = [n for n in result.notes if n.startswith("WARNING")]
    assert any("uppercase" in n.lower() for n in warning_notes)


def test_s3_aws_notes_mention_license_requirement():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    result = filestore.apply(answers)
    assert any("license" in n.lower() for n in result.notes)


def test_s3_compatible_non_aws_notes_mention_proxy_exclusion():
    answers = Answers()
    answers.filestore.type = FilestoreType.S3
    answers.filestore.s3_compatible_non_aws = True
    answers.filestore.s3_endpoint = "https://minio.internal:9000"
    result = filestore.apply(answers)
    assert any("no-proxy" in n.lower() or "nonproxyhost" in n.lower() for n in result.notes)


def test_gcs_notes_mention_license_requirement():
    answers = Answers()
    answers.filestore.type = FilestoreType.GCS
    result = filestore.apply(answers)
    assert any("license" in n.lower() for n in result.notes)


def test_azure_notes_mention_license_requirement():
    answers = Answers()
    answers.filestore.type = FilestoreType.AZURE_BLOB
    result = filestore.apply(answers)
    assert any("license" in n.lower() for n in result.notes)


def test_azure_custom_endpoint_warns_about_enterprise_license():
    answers = Answers()
    answers.filestore.type = FilestoreType.AZURE_BLOB
    answers.filestore.azure_endpoint = "https://myacct.blob.core.usgovcloudapi.net"
    result = filestore.apply(answers)
    warning_notes = [n for n in result.notes if n.startswith("WARNING")]
    assert any("enterprise" in n.lower() for n in warning_notes)
    persistence = result.values["artifactory"]["artifactory"]["persistence"]
    assert persistence["azureBlob"]["endpoint"] == "https://myacct.blob.core.usgovcloudapi.net"


def test_azure_default_endpoint_has_no_enterprise_warning():
    answers = Answers()
    answers.filestore.type = FilestoreType.AZURE_BLOB
    result = filestore.apply(answers)
    warning_notes = [n for n in result.notes if n.startswith("WARNING")]
    assert not any("enterprise" in n.lower() for n in warning_notes)


def test_nfs_notes_mention_permission_and_mount_option_gotchas():
    answers = Answers()
    answers.filestore.type = FilestoreType.NFS
    answers.filestore.nfs_ip = "10.0.0.5"
    result = filestore.apply(answers)
    assert any("root_squash" in n.lower() for n in result.notes)
