"""RabbitMQ mode for Xray: bundled classic queues, bundled quorum queues,
or a fully external RabbitMQ.

Key paths confirmed from `stable/jfrog-platform/values.yaml`:

- Quorum queues: `global.rabbitmq.xray.rabbitmq.haQuorum.enabled: true`
  (shared between the global block and Xray so both sides agree on vhost).
- External RabbitMQ: `xray.rabbitmq.enabled: false` +
  `xray.rabbitmq.external.secrets.{username,password,url}` secret refs
  (the same pattern the chart itself uses to hand Xray the bundled
  RabbitMQ's credentials, just pointed at a secret you create yourself).
"""

from __future__ import annotations

from jfvb.answers import Answers, RabbitMQMode
from jfvb.rules.base import RuleResult, SetupStep

name = "ha"


def applies(answers: Answers) -> bool:
    return answers.products.xray


def apply(answers: Answers) -> RuleResult:
    notes: list[str] = []
    steps: list[SetupStep] = []
    values: dict = {}

    if answers.rabbitmq_mode == RabbitMQMode.BUNDLED_QUORUM:
        values["global"] = {
            "rabbitmq": {"xray": {"rabbitmq": {"haQuorum": {"enabled": True}}}}
        }
        notes.append(
            "RabbitMQ quorum queues enabled "
            "(global.rabbitmq.xray.rabbitmq.haQuorum.enabled: true) for "
            "improved durability over classic mirrored queues during "
            "RabbitMQ pod restarts/upgrades."
        )

    elif answers.rabbitmq_mode == RabbitMQMode.EXTERNAL:
        secret_name = f"{answers.release_name}-external-rabbitmq-creds"
        values["rabbitmq"] = {"enabled": False}
        values["xray"] = {
            "rabbitmq": {
                "enabled": False,
                "external": {
                    "secrets": {
                        "username": {"name": secret_name, "key": "username"},
                        "password": {"name": secret_name, "key": "password"},
                        "url": {"name": secret_name, "key": "url"},
                    }
                },
            }
        }
        notes.append(
            "External RabbitMQ selected: bundled RabbitMQ disabled "
            "(rabbitmq.enabled: false, xray.rabbitmq.enabled: false), Xray "
            f"reads connection details from the '{secret_name}' secret."
        )
        steps.append(
            SetupStep(
                phase="database",
                title="Create the external RabbitMQ credentials secret",
                commands=[
                    f"kubectl -n {answers.namespace} create secret generic {secret_name} \\",
                    "  --from-literal=username='<RABBITMQ_USERNAME>' \\",
                    "  --from-literal=password='<RABBITMQ_PASSWORD>' \\",
                    "  --from-literal=url='amqp://<RABBITMQ_HOST>:5672/xray'",
                ],
                explanation="Xray reads its RabbitMQ connection details from this secret instead of the bundled chart-managed instance.",
            )
        )

    else:
        notes.append("RabbitMQ: bundled, classic mirrored queues (chart default).")

    return RuleResult(values=values, notes=notes, steps=steps)
