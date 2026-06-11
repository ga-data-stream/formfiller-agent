import pytest
from formfiller.models import QuestionType, FormQuestion, FormSchema, EmailMessage


@pytest.fixture
def sample_schema():
    return FormSchema(
        url="https://forms.office.com/r/sample",
        title="Vendor Onboarding",
        questions=(
            FormQuestion(id="q1", label="Company legal name", type=QuestionType.TEXT, required=True),
            FormQuestion(id="q2", label="VAT number", type=QuestionType.TEXT, required=True),
            FormQuestion(id="q3", label="Nickname (optional)", type=QuestionType.TEXT, required=False),
        ),
    )


@pytest.fixture
def sample_email():
    return EmailMessage(
        entry_id="ENTRY-1",
        sender="client@acme.com",
        subject="Please complete our vendor form",
        received="2026-06-10T09:00:00",
        body_text="Hello, please fill https://forms.office.com/r/sample thanks",
        body_html='<p>Hello, please fill <a href="https://forms.office.com/r/sample">here</a></p>',
    )
