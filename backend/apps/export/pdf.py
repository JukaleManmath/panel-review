import logging
from django.http import HttpResponse
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def generate_pdf(review) -> HttpResponse:
    """
    Render a PDF report for a completed review.
    Returns an HttpResponse with Content-Type: application/pdf.
    """
    from weasyprint import HTML  # lazy: cairocffi dlopen at call time, not startup

    context = _build_context(review)
    html_string = render_to_string('pdf_report.html', context)

    pdf_bytes = HTML(string=html_string).write_pdf()

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = (
        f'attachment; filename="roast-{review.share_slug}.pdf"'
    )

    logger.info('PDF generated for review %s', review.id)
    return response


def _build_context(review) -> dict:
    synthesis     = review.synthesis or {}
    agent_results = review.agent_results or {}

    return {
        'review':      review,
        'synthesis':   synthesis,
        'score':       synthesis.get('overall_score', 0),
        'summary':     synthesis.get('summary', ''),
        'critical':    synthesis.get('critical', []),
        'warnings':    synthesis.get('warnings', []),
        'suggestions': synthesis.get('suggestions', []),
        'conflicts':   synthesis.get('conflicts', []),
        'agents': [
            {
                'name':     name,
                'label':    _agent_label(name),
                'result':   agent_results.get(name, {}),
                'issues':   agent_results.get(name, {}).get('issues', []),
                'summary':  agent_results.get(name, {}).get('summary', ''),
            }
            for name in ['pragmatist', 'paranoid', 'minimalist', 'optimizer', 'mentor']
            if name in agent_results
        ],
    }


def _agent_label(name: str) -> str:
    return {
        'pragmatist': 'Staff Backend Engineer',
        'paranoid':   'Penetration Tester',
        'minimalist': 'Clean Code Evangelist',
        'optimizer':  'Performance Engineer',
        'mentor':     'Senior Onboarding Engineer',
    }.get(name, name.capitalize())
