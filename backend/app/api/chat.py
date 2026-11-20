from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import resolve_source
from app.api.sql import llm_dependency
from app.core.workspace import get_llm_workspace_id, get_workspace_id
from app.schemas import ChatRequest, ConversationDetail, ConversationSummary
from app.services import analyst_workflow, blocked_service, context_service, conversation_service, schema_service
from app.services.analyst_workflow import ChatResponse
from app.services.llm_service import LLMClient, LLMError

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/query", response_model=ChatResponse)
def chat_query(
    req: ChatRequest,
    workspace_id: str = Depends(get_llm_workspace_id),
    llm: LLMClient = Depends(llm_dependency),
):
    """Ask a question, or answer a clarification / ask a follow-up by passing `conversation_id`."""
    datasource, connector = resolve_source(req.datasource_id, workspace_id)

    history = []
    conversation_id = req.conversation_id
    if conversation_id:
        conversation = conversation_service.get_conversation(conversation_id, workspace_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        if conversation.datasource_id != req.datasource_id:
            raise HTTPException(status_code=400, detail="This conversation belongs to a different data source.")
        history = conversation_service.recent_history(conversation_id)

    schema = schema_service.build_schema(datasource, connector)
    try:
        response = analyst_workflow.handle_message(
            llm, datasource, connector, schema, req.message, history,
            entries=context_service.list_entries(workspace_id, datasource.id),
            blocked=blocked_service.effective(workspace_id, datasource.id))
    except LLMError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    analysis = analyst_workflow.add_analysis(llm, req.message, response, req.include_summary)

    # Stored only once the message was processed, so an LLM outage leaves no half-empty conversation.
    if conversation_id is None:
        conversation_id = conversation_service.create_conversation(workspace_id, req.datasource_id, req.message)
    turn = conversation_service.add_turn(
        conversation_id, req.message, response, summary=analysis.summary if analysis else None)
    return ChatResponse(
        **response.model_dump(), conversation_id=conversation_id, turn_id=turn.id, analysis=analysis)


@router.get("/conversations", response_model=list[ConversationSummary])
def list_conversations(
    datasource_id: str | None = Query(None),
    workspace_id: str = Depends(get_workspace_id),
):
    """The user's conversations, newest first, optionally for one data source."""
    return conversation_service.list_conversations(workspace_id, datasource_id)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: str, workspace_id: str = Depends(get_workspace_id)):
    """A conversation with every question in it and how each was answered."""
    conversation = conversation_service.get_conversation(conversation_id, workspace_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return ConversationDetail(**conversation.model_dump(), turns=conversation_service.list_turns(conversation_id))


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, workspace_id: str = Depends(get_workspace_id)):
    """Delete a conversation and any results kept for it."""
    if not conversation_service.delete_conversation(conversation_id, workspace_id):
        raise HTTPException(status_code=404, detail="Conversation not found.")
