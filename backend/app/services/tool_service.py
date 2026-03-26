from __future__ import annotations


def _stable_bucket(value: str, size: int) -> int:
    return sum(ord(char) for char in value.strip().upper()) % size


APPLICATION_STATUSES = [
    ("UNDER_REVIEW", "Your card application is still under review. We usually recommend checking again later today."),
    ("APPROVED", "Your card application has been approved. Please check the Atome app for the next onboarding steps."),
    ("NEEDS_INFO", "Your card application needs additional information. Please review the in-app prompts and resubmit the required details."),
    ("DECLINED", "Your card application was not approved in the latest review. You can consult support for the specific decline reason."),
]

TRANSACTION_STATUSES = [
    ("DECLINED", "The card transaction was declined by the issuer or merchant flow. Please verify the merchant, available limit, and card controls before retrying."),
    ("PENDING", "The card transaction is still pending. If the charge is unsuccessful, it is typically reversed or refunded automatically within 14 days."),
    ("REVERSED", "The card transaction has already been reversed, so no further action should be required."),
    ("SETTLED", "The card transaction was successfully settled. If you still need help, contact support with the merchant and timestamp details."),
]


def get_application_status(reference_id: str) -> dict[str, str]:
    status, detail = APPLICATION_STATUSES[_stable_bucket(reference_id, len(APPLICATION_STATUSES))]
    return {
        "reference_id": reference_id,
        "status": status,
        "detail": detail,
    }


def get_card_transaction_status(reference_id: str) -> dict[str, str]:
    status, detail = TRANSACTION_STATUSES[_stable_bucket(reference_id, len(TRANSACTION_STATUSES))]
    return {
        "reference_id": reference_id,
        "status": status,
        "detail": detail,
    }
