"""Training routes inherit the application's admin session and CSRF middleware."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from shopaware.training import AnnotationInput, SessionInput, TrainingStore


def training_router(get_database, capture_frame):
    router = APIRouter(prefix='/training', tags=['training'])

    def invoke(method, *args, **kwargs):
        try:
            return getattr(TrainingStore(get_database()), method)(*args, **kwargs)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get('')
    def overview(camera_id: str):
        return invoke('overview', camera_id)

    @router.post('/sessions', status_code=201)
    def create(payload: SessionInput):
        return invoke('create_session', payload)

    @router.post('/sessions/{session_id}/capture', status_code=201)
    def capture(session_id: str):
        session = invoke('session', session_id)
        frame, captured_at = capture_frame(session['camera_id'])
        return invoke('capture', session_id, frame, captured_at)

    @router.delete('/sessions/{session_id}')
    def delete_session(session_id: str):
        invoke('delete', session_id, session=True)
        return {'deleted': True}

    @router.get('/samples/{sample_id}/image')
    def image(sample_id: str):
        return Response(invoke('image', sample_id), media_type='image/jpeg', headers={'Cache-Control': 'no-store'})

    @router.put('/samples/{sample_id}')
    def annotate(sample_id: str, payload: AnnotationInput, request: Request):
        invoke('annotate', sample_id, payload, request.state.session['username'])
        return {'reviewed': True}

    @router.delete('/samples/{sample_id}')
    def delete_sample(sample_id: str):
        invoke('delete', sample_id)
        return {'deleted': True}

    @router.get('/export')
    def export(camera_id: str):
        return Response(invoke('export', camera_id), media_type='application/zip', headers={
            'Cache-Control': 'no-store', 'Content-Disposition': 'attachment; filename="shopaware-camera-dataset.zip"'})

    return router
