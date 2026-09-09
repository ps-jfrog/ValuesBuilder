"""Artifactory filestore / binarystore configuration.

Scoped to Artifactory ONLY: Xray, Distribution, and Catalog do not expose
configurable binarystore/filestore settings in the chart, so this module
never writes into those subcharts, per explicit product decision.

Every `persistence.type` string and key path below was CONFIRMED against
the real chart -- both the commented `artifactory.persistence` block in
values.yaml (the authoritative "supported types" list) and the actual
Go-template conditionals in `files/binarystore.xml` that decide what XML
gets generated for each type. This matters because the two aren't always
the same as Artifactory's own (non-Helm) binary-provider template names --
see the S3/Azure notes below for the two places they diverge.

  file-system (default)         -- local PVC. Single-node only: the chart's
                                    binarystore.xml has NO clustering logic
                                    for plain "file-system", so with >1
                                    replica and per-pod PVCs each node would
                                    silently run its own disconnected
                                    filestore. HA local disk requires:
  cluster-file-system            -- each node's own local disk, kept in
                                    sync over the network via the
                                    Sharding-Cluster provider + `redundancy`.
  nfs                            -- nfs.ip / haDataMount / capacity; shared
                                    mount, so no cluster-* variant needed.

  Every cloud provider (S3/GCS/Azure Blob) exposes the same direct /
  eventual / cluster split -- see FilestoreMode's docstring for the
  rationale and the divergences (S3's eventual `persistence.type` is
  `aws-s3-v3`, NOT `s3-storage-v3`; Azure's eventual/cluster fall back to
  the V1 provider because the chart never wires up a V2 eventual/cluster
  chain). S3 additionally exposes `s3-storage-v3-archive` (FilestoreMode.ARCHIVE).

  full_db     -- binaries stored as blobs in the database (Full-DB /
                 Full-DB-Direct). Not a native chart `persistence.type` --
                 the chart's binarystore.xml has no full-db branch at all --
                 so this module generates the raw binarystore.xml content
                 for it and wires it in via customBinarystoreXmlSecret.
  custom      -- customBinarystoreXmlSecret, unvalidated. Also the
                 recommended path for filestore sharding (double-shards,
                 redundant-shards, S3 sharding, state-aware) -- these need
                 per-mount/per-bucket topology info this wizard doesn't
                 collect, and JFrog's own docs say to contact JFrog Support
                 for custom chain configurations, so jfvb only points at
                 the relevant doc pages rather than guessing at the XML.

CONFIRMED via `helm template`: all of the above live under the
DOUBLE-nested `artifactory.artifactory.persistence.*` on the jfrog-platform
chart, not `artifactory.persistence.*` -- the standalone Artifactory
chart's templates read `.Values.artifactory.persistence.*` (`persistence:`
sits inside that chart's own `artifactory:` app section), and the platform
chart mounts that whole standalone chart under its own `artifactory:` key,
adding one more level of nesting.

S3-COMPATIBLE (NON-AWS) STORAGE (MinIO, Ceph, Wasabi, Dell ECS, NetApp
StorageGRID, etc.) -- confirmed from docs.jfrog.com/installation/docs/
amazon-s3-template-parameters and real-world binarystore.xml fixes for
MinIO (JFrog support threads):
  - `enablePathStyleAccess` MUST be true. AWS S3 supports virtual-hosted
    -style addressing (bucket.s3.amazonaws.com) as well as path-style
    (s3.amazonaws.com/bucket); nearly every non-AWS S3-compatible server
    ONLY understands path-style, so leaving this at the chart's `false`
    default silently breaks bucket access.
  - `useInstanceCredentials` defaults to `true` in the chart (confirmed in
    values.yaml AND binarystore.xml, which renders it explicitly either
    way) -- i.e. Artifactory tries the AWS EC2/EKS instance-metadata IAM
    chain by default. That chain doesn't exist outside AWS, so it MUST be
    explicitly set to `false` for non-AWS endpoints (and for AWS static
    -key setups too -- see the credentials note below, this was a real gap
    in this module before this pass).
  - `useHttp` + `port` are needed for on-prem HTTP (no TLS) endpoints --
    confirmed by a real MinIO "port 443 vs 9000" connection failure that
    was fixed by setting both explicitly instead of relying on the
    endpoint URL's scheme/port alone.
  - KMS (`kmsServerSideEncryptionKeyId`/`kmsKeyRegion`/`kmsCryptoMode`) and
    CloudFront fields are AWS-only and meaningless against a non-AWS
    endpoint -- this module never sets them when s3_compatible_non_aws.
  - LICENSING: JFrog's own docs state only official AWS endpoints
    (`*.amazonaws.com`, `*.amazonaws.com.cn`) are supported on every
    subscription tier -- other S3-compatible endpoints need JFrog
    Enterprise/Enterprise+.
  - Direct Cloud Storage Download is only documented for AWS/Azure/GCP;
    behavior against a non-AWS S3-compatible endpoint isn't covered by
    JFrog's docs, so this module warns rather than assuming it works.

CREDENTIALS FOOTGUN (S3 static keys, Azure account key): unlike database
passwords (which the chart lets you reference via an existing K8s Secret),
`awsS3V3.identity`/`.credential` and `azureBlob.accountKey` are PLAIN
values.yaml fields with NO secret-ref alternative in this chart -- the
whole rendered binarystore.xml (with these values already interpolated)
gets wrapped into a Secret by the chart at apply time, but the SOURCE
values.yaml has to contain them as cleartext for that to happen. To avoid
ever putting a real secret into the generated customvalues.yaml (which
might get committed to git), this module deliberately leaves these fields
OUT of the file and instead generates a `--set-string` runbook step so the
real value is supplied only at `helm upgrade --install` time -- or points
at the `custom` filestore type (your own binarystore.xml + Secret) as the
one way to keep the credential in an actual Secret object.
"""

from __future__ import annotations

from jfvb.answers import Answers, FilestoreMode, FilestoreType, SizeTier
from jfvb.refdata import get_tier
from jfvb.rules.base import RuleResult, SetupStep

name = "filestore"

# Every cloud provider follows a direct/eventual/cluster naming pattern,
# but the exact strings diverge from Artifactory's own (non-Helm) binary
# provider template names in two places -- both confirmed by reading the
# chart's actual binarystore.xml template, not just its values.yaml
# comment:
#   - S3 eventual is "aws-s3-v3" (the raw provider template is called
#     "s3-storage-v3", but that string is NOT a valid chart persistence.type)
#   - Azure eventual/cluster only have V1 branches in the chart template
#     (no "azure-blob-storage-v2"/"cluster-azure-blob-storage-v2" chain
#     exists in binarystore.xml, even though Artifactory itself supports
#     hand-written V2 eventual/cluster binarystore.xml outside the chart)
_CLOUD_PERSISTENCE_TYPES: dict[str, dict[FilestoreMode, str]] = {
    "s3": {
        FilestoreMode.DIRECT: "s3-storage-v3-direct",
        FilestoreMode.EVENTUAL: "aws-s3-v3",
        FilestoreMode.CLUSTER: "cluster-s3-storage-v3",
        FilestoreMode.ARCHIVE: "s3-storage-v3-archive",
    },
    "gcs": {
        FilestoreMode.DIRECT: "google-storage-v2-direct",
        FilestoreMode.EVENTUAL: "google-storage-v2",
        FilestoreMode.CLUSTER: "cluster-google-storage-v2",
    },
    "azure_blob": {
        FilestoreMode.DIRECT: "azure-blob-storage-v2-direct",
        FilestoreMode.EVENTUAL: "azure-blob",
        FilestoreMode.CLUSTER: "cluster-azure-blob-storage",
    },
}


def applies(answers: Answers) -> bool:
    return True


def _direct_download_fields(answers: Answers) -> dict:
    """`enableSignedUrlRedirect`/`signedUrlExpirySeconds` for S3/GCS -- the
    chart templates these straight into binarystore.xml (confirmed in both
    providers' chain blocks), letting Artifactory redirect large-binary
    downloads (e.g. Docker layers) straight to cloud storage instead of
    proxying them through Artifactory pods. Repo-level `downloadRedirect`
    still needs to be turned on separately after install -- that's a
    per-repository API/UI setting, not something in values.yaml.
    """

    if not answers.filestore.direct_cloud_download:
        return {}
    return {
        "enableSignedUrlRedirect": True,
        "signedUrlExpirySeconds": answers.filestore.signed_url_expiry_seconds,
    }


def _direct_download_notes(answers: Answers) -> list[str]:
    if not answers.filestore.direct_cloud_download:
        return []
    return [
        "Direct Cloud Storage Download enabled (enableSignedUrlRedirect, "
        f"{answers.filestore.signed_url_expiry_seconds}s signed URLs) -- "
        "Artifactory can respond to eligible download requests with a "
        "redirect straight to cloud storage instead of proxying the bytes "
        "itself, cutting load/egress on Artifactory pods for large "
        "binaries. This is OFF per-repository until you also flip on "
        "'Enable Redirect Download' (downloadRedirect) for the specific "
        "repositories you want it for -- see the runbook step.",
    ]


def _direct_download_step(answers: Answers) -> SetupStep | None:
    if not answers.filestore.direct_cloud_download:
        return None
    return SetupStep(
        phase="filestore",
        title="Turn on direct-download redirect for specific repositories",
        commands=[
            "curl -u <ADMIN_USER>:<ADMIN_PASSWORD_OR_TOKEN> -X POST \\",
            "  '<JFROG_URL>/artifactory/api/repositories/<REPO_KEY>/downloadRedirect' \\",
            "  -H 'Content-Type: application/json' -d '{\"downloadRedirect\": true}'",
        ],
        explanation=(
            "enableSignedUrlRedirect in values.yaml only makes the "
            "capability available -- it still needs to be turned on "
            "per-repository (UI: Advanced tab > 'Enable Redirect Download', "
            "or the REST/YAML repo config) for it to take effect. Remote "
            "repositories additionally need 'Store Artifacts Locally' on."
        ),
    )


def _out_of_band_credential_step(
    answers: Answers, title: str, value_paths: list[str], placeholders: list[str]
) -> SetupStep:
    """A `--set-string` runbook step for credential fields the chart has no
    secret-ref for (see the module docstring's CREDENTIALS FOOTGUN note) --
    keeps the real value out of the generated customvalues.yaml entirely.
    """

    set_args = " \\\n  ".join(
        f"--set-string {path}='{placeholder}'"
        for path, placeholder in zip(value_paths, placeholders)
    )
    return SetupStep(
        phase="filestore",
        title=title,
        commands=[
            f"helm upgrade --install {answers.release_name} jfrog/jfrog-platform \\",
            f"  --namespace {answers.namespace} \\",
            f"  {set_args} \\",
            "  -f customvalues.yaml",
        ],
        explanation=(
            "This chart has no existing-Secret field for this credential -- "
            "it's a plain values.yaml field with no secret-ref alternative, "
            "so the ONLY way to avoid writing it in cleartext into "
            "customvalues.yaml is to pass it at install/upgrade time like "
            "this instead (or via a separate --values file that's kept out "
            "of git). If you need it to live in an actual Kubernetes "
            "Secret object, use the `custom` filestore type with your own "
            "binarystore.xml + Secret instead."
        ),
    )


def _migration_note(answers: Answers, target_label: str) -> list[str]:
    if answers.fresh_deployment:
        return []
    return [
        f"This is NOT a fresh deployment -- if an existing Artifactory "
        f"instance is already running with a different filestore and "
        f"you're changing it to {target_label}, you need a proper filestore "
        "migration (JFrog's 'Filestore Migration to S3/Google Storage' or "
        "'Manual Filestore Migration for Azure Blob Storage' docs), NOT "
        "just applying this values.yaml -- simply switching "
        "persistence.type does not move existing binaries for you."
    ]


def _artifactory_replica_count(answers: Answers) -> int:
    if answers.size_tier == SizeTier.CUSTOM:
        return int((answers.custom_sizing or {}).get("artifactory_replicas", 1))
    try:
        return get_tier(answers.size_tier.value)["artifactory"]["replicas"]
    except KeyError:
        return 1


def _mode_notes(cloud_label: str, mode: FilestoreMode, persistence_type: str, replicas: int) -> list[str]:
    """Rationale + HA-fit warnings for whichever upload mode was picked --
    see FilestoreMode's docstring for the underlying trade-offs.
    """

    notes = [f"Filestore: {cloud_label} (type: {persistence_type})."]

    if mode == FilestoreMode.DIRECT:
        notes.append(
            "Direct upload mode -- JFrog's recommended default. Binaries go "
            "straight to the bucket/container with no local buffering, and "
            "no shared mount is needed for either single-node or HA."
        )
    elif mode == FilestoreMode.EVENTUAL:
        notes.append(
            "Eventual upload mode -- binaries are buffered locally, then "
            "uploaded asynchronously. Only worth choosing over Direct if "
            "upload bandwidth to the cloud is slow/unreliable, or writes "
            "must succeed while the cloud endpoint is temporarily down."
        )
        if replicas > 1:
            notes.append(
                f"WARNING: Eventual mode was selected with {replicas} "
                "Artifactory replicas (HA) -- Eventual REQUIRES a shared "
                "mount across every node for the local buffering directory "
                "in HA, or nodes can serve stale/missing binaries to each "
                "other. Switch to Direct or Cluster mode unless that shared "
                "mount already exists."
            )
    elif mode == FilestoreMode.CLUSTER:
        notes.append(
            "Cluster upload mode -- HA-oriented: each node buffers locally "
            "and syncs independently via the eventual-cluster binary "
            "provider, so no shared mount is needed. JFrog recommends "
            "Direct mode over Cluster wherever possible; Cluster mainly "
            "exists for environments already built around that model."
        )
        if replicas <= 1:
            notes.append(
                "WARNING: Cluster mode was selected but this deployment is "
                "single-node (1 replica) -- Cluster mode has no benefit "
                "here and just adds unnecessary local buffering. Switch to "
                "Direct mode."
            )
    else:  # ARCHIVE (S3 only)
        notes.append(
            "Archive mode (s3-storage-v3-archive) -- a real, chart-supported "
            "persistence type, but JFrog's public docs don't fully spell "
            "out its behavior beyond the binarystore.xml chain shape. "
            "LOW CONFIDENCE: verify the exact semantics with JFrog Support "
            "or your target chart version's docs before relying on this in "
            "production. If you're after S3 cost tiering, note that plain "
            "S3 Intelligent-Tiering (via an S3 lifecycle rule, using the "
            "*automatic* access tiers only) needs NO Artifactory-side "
            "changes at all and works fine with plain Direct mode -- that's "
            "likely the simpler, better-documented path for that goal."
        )

    return notes


def apply(answers: Answers) -> RuleResult:
    fs = answers.filestore
    notes: list[str] = []
    steps: list[SetupStep] = []
    persistence: dict = {}

    if fs.type == FilestoreType.LOCAL:
        replicas = _artifactory_replica_count(answers)
        if replicas > 1:
            persistence = {"type": "cluster-file-system"}
            notes.append(
                f"Filestore: local disk, HA mode (type: cluster-file-system) "
                f"-- {replicas} Artifactory replicas each keep their own "
                "local disk, kept in sync over the network via the "
                "Sharding-Cluster provider (artifactory.persistence.redundancy "
                "controls how many copies of each artifact are kept)."
            )
            notes.append(
                "IMPORTANT: plain 'file-system' does NOT cluster on its "
                "own -- the chart's binarystore.xml has no synchronization "
                "logic for it, so with >1 replica and per-pod PVCs each "
                "node would silently run its own disconnected filestore. "
                "cluster-file-system was selected automatically because "
                "this is an HA deployment."
            )
            notes.append(
                "Network-synced local disk works, but doesn't scale "
                "storage capacity/cost the way a cloud or NFS-backed "
                "filestore does, and adds cross-node replication traffic. "
                "Worth reconsidering S3/GCS/Azure Blob or NFS for anything "
                "beyond eval/small deployments."
            )
        else:
            notes.append(
                "Filestore: local PVC (file-system, chart default) -- "
                "single Artifactory replica, so no clustering/replication "
                "is needed. If you later scale to more than one replica, "
                "jfvb will switch this to cluster-file-system automatically "
                "-- rerun the wizard (or re-check --validate output) before "
                "scaling up."
            )
            if fs.local_cache_enabled:
                persistence = {"fileSystem": {"cache": {"enabled": True}}}
                notes.append(
                    "Local disk cache (cache-fs) enabled in front of the "
                    "file-system filestore -- helps when the underlying "
                    "volume is slow (e.g. a mounted network disk), sized "
                    "via artifactory.persistence.maxCacheSize (default 5GB)."
                )
        return RuleResult(
            values=(
                {"artifactory": {"artifactory": {"persistence": persistence}}}
                if persistence
                else {}
            ),
            notes=notes,
            steps=steps,
        )

    if fs.type == FilestoreType.NFS:
        if not fs.nfs_ip:
            notes.append("WARNING: NFS selected but no server IP was provided.")
        persistence = {
            "type": "nfs",
            "nfs": {
                "ip": fs.nfs_ip or "<NFS_SERVER_IP>",
                "capacity": fs.nfs_capacity,
            },
        }
        notes.append(
            f"Filestore: NFS ({fs.nfs_ip or '<NFS_SERVER_IP>'}). Requires an "
            "existing NFS server reachable from every node in the cluster; "
            "the chart does not provision one for you. A local read cache "
            "in front of the NFS mount is added automatically by the chart "
            "(cache-fs, sized via artifactory.persistence.maxCacheSize) to "
            "help with slow/high-latency NFS access."
        )
        notes.append(
            "Common NFS gotcha: Artifactory's container runs as a non-root "
            "UID, so the export needs no_root_squash (or a matching "
            "UID/GID mapping) and write permissions for that UID -- "
            "otherwise pods start but every write fails with a permission "
            "error. Also prefer NFS v4.1 mount options (hard,timeo=600, "
            "retrans=2) over v3 for better lock/retry behavior under load."
        )
        notes.extend(_migration_note(answers, "NFS"))
        steps.append(
            SetupStep(
                phase="filestore",
                title="Verify NFS server reachability",
                commands=[
                    f"showmount -e {fs.nfs_ip or '<NFS_SERVER_IP>'}  # run from a node or a debug pod",
                ],
                explanation="Confirms the NFS export is reachable before install.",
            )
        )

    elif fs.type == FilestoreType.S3:
        non_aws = fs.s3_compatible_non_aws
        persistence_type = _CLOUD_PERSISTENCE_TYPES["s3"][fs.mode]
        use_iam = fs.s3_use_iam_role and not non_aws
        bucket_name = fs.s3_bucket or "<S3_BUCKET_NAME>"
        # A dotted bucket name breaks TLS hostname verification under
        # virtual-hosted-style addressing even on REAL AWS S3 -- AWS's
        # wildcard cert for *.s3.amazonaws.com only covers one subdomain
        # level, so "my.bucket.name.s3.amazonaws.com" doesn't validate.
        # This bites people on plain AWS, not just non-AWS providers, so
        # it's handled independently of s3_compatible_non_aws below.
        dotted_bucket = "." in bucket_name and not bucket_name.startswith("<")
        aws_s3v3 = {
            "region": fs.s3_region or ("us-east-1" if non_aws else "<AWS_REGION>"),
            "bucketName": bucket_name,
            "path": fs.s3_path,
            "endpoint": fs.s3_endpoint or ("<S3_ENDPOINT_URL>" if non_aws else ""),
            # Chart default is true (rendered explicitly either way by
            # binarystore.xml) -- MUST be false whenever we're not
            # deliberately relying on the AWS instance-metadata IAM chain,
            # or Artifactory silently ignores any credentials and tries
            # (and fails) that chain instead. See module docstring.
            "useInstanceCredentials": use_iam,
            **_direct_download_fields(answers),
        }
        if bucket_name != bucket_name.lower() and not bucket_name.startswith("<"):
            notes.append(
                f"WARNING: bucket name '{bucket_name}' has uppercase "
                "characters -- S3 bucket names must be all lowercase. "
                "Double-check this isn't a typo before installing."
            )
        if non_aws or dotted_bucket:
            aws_s3v3["enablePathStyleAccess"] = True
        if dotted_bucket and not non_aws:
            notes.append(
                f"WARNING: bucket name '{bucket_name}' contains dots -- "
                "enablePathStyleAccess was set to true automatically "
                "because AWS's TLS wildcard cert for "
                "*.s3.amazonaws.com/*.<region>.amazonaws.com only covers "
                "one subdomain level, so virtual-hosted-style addressing "
                "against a dotted bucket name intermittently fails "
                "certificate validation. Path-style access avoids this."
            )
        if non_aws:
            if fs.s3_use_http:
                aws_s3v3["useHttp"] = True
            if fs.s3_port:
                aws_s3v3["port"] = fs.s3_port
        persistence = {"type": persistence_type, "awsS3V3": aws_s3v3}

        provider_label = (
            f"S3-compatible storage, bucket '{fs.s3_bucket or '<S3_BUCKET_NAME>'}'"
            if non_aws
            else f"AWS S3, bucket '{fs.s3_bucket or '<S3_BUCKET_NAME>'}'"
        )
        notes.extend(
            _mode_notes(
                provider_label,
                fs.mode,
                persistence_type,
                _artifactory_replica_count(answers),
            )
        )
        if non_aws:
            notes.append(
                "Non-AWS S3-compatible provider (e.g. MinIO/Ceph/Wasabi/Dell "
                "ECS/NetApp StorageGRID) -- enablePathStyleAccess was set to "
                "true automatically: virtual-hosted-style bucket addressing "
                "(the AWS default) almost never works against non-AWS S3 "
                "servers, only path-style does."
            )
            notes.append(
                "WARNING: only official AWS endpoints (*.amazonaws.com, "
                "*.amazonaws.com.cn) are supported on every JFrog "
                "subscription tier -- other S3-compatible endpoints require "
                "JFrog Enterprise or Enterprise+. Confirm your license "
                "covers this before relying on it."
            )
            notes.append(
                "KMS server-side encryption and CloudFront fields are "
                "AWS-only and were NOT set -- they don't apply to a "
                "non-AWS S3-compatible endpoint."
            )
            if not fs.s3_endpoint:
                notes.append(
                    "WARNING: no endpoint was provided -- a custom endpoint "
                    "is MANDATORY for non-AWS S3-compatible storage (there's "
                    "no default to fall back on the way there is for real "
                    "AWS regions). Set awsS3V3.endpoint before installing."
                )
            if fs.s3_use_http:
                notes.append(
                    "Endpoint uses plain HTTP (no TLS) -- fine for local/"
                    "dev/test, but avoid for anything reachable outside a "
                    "trusted network. If instead the endpoint uses HTTPS "
                    "with an internal/private CA, make sure that CA is "
                    "added via networking.custom_trusted_certificates so "
                    "Artifactory's JVM trusts it."
                )
            if answers.filestore.direct_cloud_download:
                notes.append(
                    "WARNING: Direct Cloud Storage Download "
                    "(enableSignedUrlRedirect) is only documented by JFrog "
                    "for AWS, Azure, and GCP -- behavior against a non-AWS "
                    "S3-compatible endpoint is unverified. Test thoroughly "
                    "before relying on it in production, and disable it if "
                    "downloads don't redirect correctly."
                )
            notes.append(
                "If the cluster/nodes use an outbound HTTP(S) proxy "
                "(common in regulated or air-gapped networks), make sure "
                f"the endpoint's host ({fs.s3_endpoint or '<S3_ENDPOINT_URL>'}) "
                "is added to the no-proxy/nonProxyHost exclusion list -- "
                "otherwise Artifactory will try to reach an internal/"
                "on-prem S3-compatible endpoint through the proxy and fail. "
                "If it genuinely needs to go through a proxy instead, the "
                "provider also has its own proxyHost/proxyPort/"
                "proxyIdentity/proxyCredential fields (not set by jfvb -- "
                "add them to awsS3V3 manually if needed)."
            )
        else:
            notes.append(
                "AWS S3 Object Storage requires at least a JFrog Container "
                "Registry, Pro, Enterprise X, or Enterprise+ license "
                "(not available on the free/OSS tier) -- confirm your "
                "license covers it before relying on this filestore type."
            )
        notes.extend(_direct_download_notes(answers))
        notes.extend(_migration_note(answers, "S3" if not non_aws else "S3-compatible storage"))
        if download_step := _direct_download_step(answers):
            steps.append(download_step)

        if use_iam:
            notes.append(
                "Using IAM role-based access (no identity/credential set in "
                "values.yaml) -- ensure the node/pod service account has an "
                "IAM role/IRSA binding with S3 read/write on the bucket."
            )
        else:
            if fs.s3_use_iam_role and non_aws:
                notes.append(
                    "WARNING: IAM role-based access was requested, but "
                    "there's no AWS EC2/EKS instance-metadata service "
                    "outside AWS -- switched to static access key/secret "
                    "key credentials automatically (useInstanceCredentials: "
                    "false). You still need to supply the real access key "
                    "and secret key -- see the runbook step below."
                )
            else:
                notes.append(
                    "WARNING: static access key/secret key credentials "
                    "(identity/credential) selected instead of IAM -- "
                    "useInstanceCredentials was explicitly set to false so "
                    "Artifactory doesn't default to (and fail) the AWS "
                    "instance-metadata IAM chain. The actual key values are "
                    "NOT written into this file (no secret-ref field exists "
                    "for them in this chart) -- supply them at install time "
                    "via the runbook step below instead."
                )
            steps.append(
                _out_of_band_credential_step(
                    answers,
                    "Supply S3 access key/secret key at install time (kept out of values.yaml)",
                    [
                        "artifactory.artifactory.persistence.awsS3V3.identity",
                        "artifactory.artifactory.persistence.awsS3V3.credential",
                    ],
                    ["<S3_ACCESS_KEY>", "<S3_SECRET_ACCESS_KEY>"],
                )
            )
        steps.append(
            SetupStep(
                phase="filestore",
                title="Verify S3 bucket access",
                commands=(
                    [
                        f"aws --endpoint-url {fs.s3_endpoint or '<S3_ENDPOINT_URL>'} "
                        f"s3 ls s3://{fs.s3_bucket or '<S3_BUCKET_NAME>'}/{fs.s3_path}",
                    ]
                    if non_aws
                    else [
                        f"aws s3 ls s3://{fs.s3_bucket or '<S3_BUCKET_NAME>'}/{fs.s3_path} "
                        "--region " + (fs.s3_region or "<AWS_REGION>"),
                    ]
                ),
                explanation="Confirms the bucket/path exists and is reachable with the credentials/role the cluster will use.",
            )
        )

    elif fs.type == FilestoreType.GCS:
        persistence_type = _CLOUD_PERSISTENCE_TYPES["gcs"][fs.mode]
        persistence = {
            "type": persistence_type,
            "googleStorage": {
                "bucketName": fs.gcs_bucket or "<GCS_BUCKET_NAME>",
                "path": fs.gcs_path,
                "gcpServiceAccount": {"enabled": True},
                **_direct_download_fields(answers),
            },
        }
        notes.extend(
            _mode_notes(
                f"Google Cloud Storage, bucket '{fs.gcs_bucket or '<GCS_BUCKET_NAME>'}'",
                fs.mode,
                persistence_type,
                _artifactory_replica_count(answers),
            )
        )
        notes.append(
            "Requires a GCP service-account key supplied via "
            "artifactory.persistence.googleStorage.gcpServiceAccount "
            "(existing secret or inline JSON)."
        )
        notes.append(
            "Google Cloud Storage as a filestore requires at least a "
            "JFrog Pro, Enterprise X, or Enterprise+ license -- it's not "
            "available on the OSS/Community Edition tier. Confirm your "
            "license covers it before relying on this filestore type."
        )
        notes.extend(_direct_download_notes(answers))
        notes.extend(_migration_note(answers, "Google Cloud Storage"))
        if download_step := _direct_download_step(answers):
            steps.append(download_step)
        steps.append(
            SetupStep(
                phase="filestore",
                title="Create the GCP service-account secret for GCS access",
                commands=[
                    f"kubectl -n {answers.namespace} create secret generic gcs-service-account \\",
                    "  --from-file=gcp-service-account.json=<PATH_TO_SERVICE_ACCOUNT_JSON>",
                ],
                explanation="The GCS-backed binary provider authenticates using this service-account key.",
            )
        )

    elif fs.type == FilestoreType.AZURE_BLOB:
        persistence_type = _CLOUD_PERSISTENCE_TYPES["azure_blob"][fs.mode]
        persistence = {
            "type": persistence_type,
            "azureBlob": {
                "accountName": fs.azure_account_name or "<AZURE_ACCOUNT_NAME>",
                "containerName": fs.azure_container or "<AZURE_CONTAINER_NAME>",
                "endpoint": fs.azure_endpoint or "",
            },
        }
        notes.extend(
            _mode_notes(
                f"Azure Blob Storage, container '{fs.azure_container or '<AZURE_CONTAINER_NAME>'}'",
                fs.mode,
                persistence_type,
                _artifactory_replica_count(answers),
            )
        )
        if fs.mode == FilestoreMode.DIRECT:
            notes.append(
                "Uses the newer Azure Blob Storage V2 Binary Provider "
                "(needs Artifactory >= 7.49). On an older Artifactory "
                "version, use `custom` filestore type with your own V1 "
                "binarystore.xml (`azure-blob-storage-direct`) instead."
            )
        else:
            notes.append(
                "NOTE: this uses the older Azure Blob Storage V1 Binary "
                "Provider -- the chart's binarystore.xml only wires up a V2 "
                "chain for Direct mode, so Eventual/Cluster fall back to "
                "V1. V1 is still fully supported by Artifactory, but if you "
                "specifically need V2 (e.g. for its SAS-token support), "
                "Direct mode is currently the only chart-native way to get "
                "it -- otherwise supply your own V2 eventual/cluster "
                "binarystore.xml via the `custom` filestore type."
            )
        notes.append(
            "Azure Blob Storage as a filestore requires at least a JFrog "
            "Container Registry, Pro, Enterprise X, or Enterprise+ license "
            "-- it's not available on the OSS/Community Edition tier."
        )
        if fs.azure_endpoint:
            endpoint_desc = (
                "was left as the '<AZURE_BLOB_ENDPOINT>' placeholder -- fill "
                "in the actual value before installing"
                if fs.azure_endpoint == "<AZURE_BLOB_ENDPOINT>"
                else f"was set to '{fs.azure_endpoint}'"
            )
            notes.append(
                f"WARNING: a custom endpoint {endpoint_desc} instead of the "
                "default blob.core.windows.net -- custom Azure endpoints "
                "require an Enterprise or Enterprise+ subscription. Confirm "
                "your license before installing, or leave endpoint unset to "
                "use the default."
            )
        notes.extend(_migration_note(answers, "Azure Blob Storage"))
        notes.append(
            "WARNING: the storage account key is NOT written into "
            "values.yaml -- azureBlob.accountKey is a plain field with no "
            "existing-Secret alternative in this chart, so jfvb keeps it "
            "out of the generated file entirely. Supply it at install time "
            "instead -- see the runbook step below."
        )
        steps.append(
            _out_of_band_credential_step(
                answers,
                "Supply the Azure storage account key at install time (kept out of values.yaml)",
                ["artifactory.artifactory.persistence.azureBlob.accountKey"],
                ["<AZURE_STORAGE_ACCOUNT_KEY>"],
            )
        )

    elif fs.type == FilestoreType.FULL_DB:
        secret_name = "artifactory-full-db-binarystore"
        template_name = "full-db" if fs.full_db_use_cache else "full-db-direct"
        if fs.full_db_use_cache:
            xml_lines = [
                '<config version="v1">',
                '    <chain template="full-db"/>',
                '    <provider id="cache-fs" type="cache-fs">',
                '        <provider id="blob" type="blob"/>',
                "    </provider>",
                "</config>",
            ]
        else:
            xml_lines = [
                '<config version="v1">',
                '    <chain template="full-db-direct"/>',
                '    <provider id="blob" type="blob"/>',
                "</config>",
            ]
        persistence = {"customBinarystoreXmlSecret": secret_name}
        notes.append(
            f"Filestore: binaries stored as blobs in the database (chain "
            f"template: {template_name}). Not a native chart "
            "`persistence.type` -- the chart's binarystore.xml has no "
            "full-db branch -- so jfvb generates the raw binarystore.xml "
            f"content and wires it in via customBinarystoreXmlSecret: "
            f"{secret_name}."
        )
        if fs.full_db_use_cache:
            notes.append(
                "Using Full-DB (with a filesystem cache layer in front of "
                "the DB reads) rather than Full-DB-Direct -- frequent "
                "requests are served from cache-fs before hitting the "
                "database, which is recommended over Full-DB-Direct for "
                "anything beyond light/eval use."
            )
        notes.append(
            "WARNING: storing binaries in the database only makes sense "
            "for small artifact sizes/volumes -- large BLOBs can cause "
            "database memory pressure (most DBs buffer BLOBs rather than "
            "streaming them) and this couples filestore capacity/backup "
            "size directly to your database. Consider file-system/NFS/S3/"
            "GCS/Azure Blob for anything beyond a small eval instance."
        )
        notes.extend(_migration_note(answers, "Full-DB"))
        steps.append(
            SetupStep(
                phase="filestore",
                title="Create the Full-DB binarystore.xml secret",
                commands=[
                    "cat <<'EOF' > binarystore.xml",
                    *xml_lines,
                    "EOF",
                    f"kubectl -n {answers.namespace} create secret generic {secret_name} \\",
                    "  --from-file=binarystore.xml=binarystore.xml",
                ],
                explanation=(
                    f"Set artifactory.persistence.customBinarystoreXmlSecret "
                    f"to '{secret_name}' (already in the generated "
                    "values.yaml) so Artifactory mounts this file instead "
                    "of generating its own."
                ),
            )
        )

    else:  # CUSTOM
        notes.append(
            "Filestore: custom/other provider -- not one of the chart's "
            "built-in binarystore types. Common reasons to land here: "
            "Filestore Sharding (double-shards, redundant-shards, "
            "sharding-cluster, S3 sharding) for horizontal scaling across "
            "multiple mounts/buckets, or a provider chain jfvb doesn't "
            "model yet. These need per-mount/per-bucket topology details "
            "this wizard doesn't collect, and JFrog's own docs recommend "
            "contacting JFrog Support to design a custom chain safely -- "
            "see docs.jfrog.com/installation/docs/filestore-sharding and "
            "the Double Shards / Redundant Shards / Sharding-Cluster / "
            "State-Aware Binary Provider pages under it. Supply a complete "
            "binarystore.xml via a Kubernetes Secret and reference it with "
            "artifactory.persistence.customBinarystoreXmlSecret. jfvb "
            "cannot generate or validate arbitrary binarystore.xml content "
            "-- you'll need to provide it yourself."
        )
        steps.append(
            SetupStep(
                phase="filestore",
                title="Create the custom binarystore.xml secret",
                commands=[
                    f"kubectl -n {answers.namespace} create secret generic custom-binarystore \\",
                    "  --from-file=binarystore.xml=<PATH_TO_BINARYSTORE_XML>",
                ],
                explanation="Set artifactory.persistence.customBinarystoreXmlSecret to this secret's name in the generated values.yaml.",
            )
        )

    values = (
        {"artifactory": {"artifactory": {"persistence": persistence}}}
        if persistence
        else {}
    )
    return RuleResult(values=values, notes=notes, steps=steps)
