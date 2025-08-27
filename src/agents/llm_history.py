from langchain_core.messages import SystemMessage, AIMessage, HumanMessage, ToolMessage
import json
import threading


class LLMHistoryManager:
    def __init__(self, system_message, summary_llm=None):
        self.base_system_message = system_message
        self.summary_llm = summary_llm  # Optional: pass in LLM to use for summarizing
        
        self._summary_lock = threading.Lock()  # Add this!
        self._summary_in_progress = False      # Optional: use as an extra guard
    
    def compress_and_filter_messages(self, messages):
        from langchain_core.messages import AIMessage, ToolMessage

        GREETING_PHRASES = [
            "welcome back!", "how can i assist", "hello", "hi", "hey", "how can i help"
        ]

        def is_greeting(msg):
            if isinstance(msg, AIMessage) and msg.content:
                text = msg.content.lower()
                return any(text.startswith(phrase) for phrase in GREETING_PHRASES)
            return False

        def compress_tool_content(msg):
            # Only compress ToolMessage contents if they are giant dicts
            if isinstance(msg, ToolMessage):
                # Try to store only a summary or the status, or use a hash if needed
                # (here, as an example, store first 150 chars or the whole if short)
                content = str(msg.content)
                if len(content) > 160:
                    short = content[:150] + "…"
                else:
                    short = content
                return ToolMessage(content=short, tool_call_id=getattr(msg, 'tool_call_id', None))
            return msg

        # Remove AIMessage greetings, compress ToolMessage content
        filtered = [compress_tool_content(msg) for msg in messages if not is_greeting(msg)]
        return filtered

    def smart_trim(self, messages, soft_cap=12):
        """
        Intelligently trims message history while preserving conversation context and tool usage patterns.
        
        This method performs two main operations:
        
        1. **Sanitization Pass**: Removes orphaned ToolMessages that don't have corresponding 
           AIMessages with matching tool_calls. This ensures OpenAI API compliance.
           
        2. **Trimming Pass**: Trims from the end of the message list while preserving:
           - Complete tool usage blocks (AIMessage with tool_calls + corresponding ToolMessages)
           - Recent conversation context
           - System messages
           
        Args:
            messages: List of message objects (SystemMessage, HumanMessage, AIMessage, ToolMessage)
            soft_cap: Maximum number of messages to keep (default: 12)
            
        Returns:
            List of messages with:
            - No orphaned ToolMessages
            - Preserved tool usage blocks
            - Recent conversation context
            - Length within soft_cap limit
            
        Example:
            Input: [SystemMessage, ToolMessage, AIMessage, HumanMessage, AIMessage]
            Output: [SystemMessage, AIMessage, HumanMessage, AIMessage]  # Orphaned ToolMessage removed
        """
        # First pass: remove orphaned ToolMessages from the beginning
        cleaned_messages = []
        for i, msg in enumerate(messages):
            if isinstance(msg, ToolMessage):
                # Check if there's a preceding AIMessage with matching tool_calls
                has_preceding_ai_with_matching_tools = False
                for j in range(i-1, -1, -1):
                    prev_msg = messages[j]
                    if isinstance(prev_msg, AIMessage) and getattr(prev_msg, 'tool_calls', None):
                        # Check if any tool_call ID matches this ToolMessage's tool_call_id
                        for tool_call in prev_msg.tool_calls:
                            tool_call_id = getattr(tool_call, 'id', None) or tool_call.get('id', None)
                            tool_message_id = getattr(msg, 'tool_call_id', None)
                            if tool_call_id == tool_message_id:
                                has_preceding_ai_with_matching_tools = True
                                break
                        if has_preceding_ai_with_matching_tools:
                            break
                # Keep ToolMessages even if we can't find the corresponding AIMessage
                # This is important for when the LLM needs to process tool results
                cleaned_messages.append(msg)
            else:
                cleaned_messages.append(msg)
        
        # Second pass: trim from the end
        trimmed = []
        idx = len(cleaned_messages) - 1
        
        while idx >= 0 and len(trimmed) < soft_cap:
            msg = cleaned_messages[idx]
            
            if isinstance(msg, ToolMessage):
                # Find the corresponding AIMessage with tool_calls
                ai_idx = None
                for j in range(idx - 1, -1, -1):
                    prev = cleaned_messages[j]
                    if (
                        isinstance(prev, AIMessage)
                        and hasattr(prev, "tool_calls")
                        and prev.tool_calls
                    ):
                        # Check if this ToolMessage corresponds to this AIMessage
                        tool_message_id = getattr(msg, 'tool_call_id', None)
                        for tool_call in prev.tool_calls:
                            tool_call_id = getattr(tool_call, 'id', None) or tool_call.get('id', None)
                            if tool_call_id == tool_message_id:
                                ai_idx = j
                                break
                        if ai_idx is not None:
                            break
                
                if ai_idx is not None:
                    # Include the complete tool usage block
                    block = cleaned_messages[ai_idx:idx+1]
                    trimmed = block + trimmed
                    idx = ai_idx - 1
                else:
                    # Orphaned ToolMessage, skip it
                    idx -= 1
            else:
                trimmed = [msg] + trimmed
                idx -= 1
        
        if not trimmed and cleaned_messages:
            trimmed = cleaned_messages[-soft_cap:]
        
        result = trimmed[-soft_cap:]
        
        # Content trimming: Create clean copies of messages without verbose metadata
        cleaned_result = []
        for msg in result:
            if isinstance(msg, AIMessage):
                clean_msg = AIMessage(
                    content=msg.content,
                    tool_calls=getattr(msg, 'tool_calls', []),
                    invalid_tool_calls=getattr(msg, 'invalid_tool_calls', [])
                )
                if hasattr(msg, 'additional_kwargs') and msg.additional_kwargs:
                    if 'tool_calls' in msg.additional_kwargs:
                        clean_msg.additional_kwargs = {'tool_calls': msg.additional_kwargs['tool_calls']}
                    else:
                        clean_msg.additional_kwargs = {}
                cleaned_result.append(clean_msg)
            elif isinstance(msg, ToolMessage):
                clean_msg = ToolMessage(
                    content=msg.content,
                    tool_call_id=getattr(msg, 'tool_call_id', None)
                )
                cleaned_result.append(clean_msg)
            else:
                # Keep other message types as-is
                cleaned_result.append(msg)
        
        # *** NEW: Apply message compression and greeting removal ***
        return self.compress_and_filter_messages(cleaned_result)

    def ensure_system_message(self, messages, state):
        """
        Updates system message with conversation summaries and ensures it's first in the list.
        Uses smart_trim to handle message sequence and orphaned ToolMessages.
        
        Args:
            messages: List of message objects (SystemMessage, HumanMessage, AIMessage, ToolMessage)
            state: AgentState containing chat_summaries
            
        Returns:
            List of messages with updated system message at the beginning and proper sequence
        """
        # Compose dynamic SystemMessage with all current summaries
        system_content = self.base_system_message.content
        if state.chat_summaries:
            summaries_text = "\n\n".join(
                [f"Conversation summary ({i+1}): {s}" for i, s in enumerate(state.chat_summaries)]
            )
            system_content = f"{system_content}\n\n{summaries_text}"

        system_msg = SystemMessage(content=system_content)
        
        # Use smart_trim to handle message sequence and remove orphaned ToolMessages
        trimmed_messages = self.smart_trim(messages)
        
        # Find and update/replace the first SystemMessage, or add one at the beginning
        if trimmed_messages and isinstance(trimmed_messages[0], SystemMessage):
            # Replace existing system message
            return [system_msg] + trimmed_messages[1:]
        else:
            # Add system message at the beginning
            return [system_msg] + trimmed_messages

    def _summarize_messages(self, messages):
        """
        Extracts persistent user facts from a batch of messages.
        """
        history_text = "\n".join(
            f"[{type(m).__name__}] {getattr(m, 'content', '')}" for m in messages
        )
        prompt = (
            "Analyze this user-assistant conversation and extract ONLY persistent facts about the user and their preferences. "
            "Return these as a numbered or bulleted list, updating existing facts if they have changed. "
            "List each fact as a bullet point starting with '-'. "
            "Do NOT include greetings, general chat, or one-off details. Focus on facts that will help you serve the user better in future interactions. "
            "Example facts: dietary preferences, favorite products, delivery address, order history, payment preferences, allergies, important constraints.\n\n"
            f"{history_text}\n\nUser facts:"
        )
        if self.summary_llm is None:
            raise RuntimeError("summary_llm must be set to use summarization")
        summary_response = self.summary_llm.invoke([HumanMessage(content=prompt)])
        return summary_response.content if hasattr(summary_response, "content") else str(summary_response)

    def _background_summary_task(self, to_summarize, state):
        """
        Run summarization and save in background.
        """
        try:
            summary = self._summarize_messages(to_summarize)
            state.chat_summaries.append(summary)
            if len(state.chat_summaries) > 3:
                state.chat_summaries.pop(0)
            # Remove summarized messages
            del state.all_time_history[:30]
            state.save()
            print(f"[DEBUG] Background summarization and save complete. Summary: {summary[:100]}...")
        except Exception as e:
            print(f"[WARNING] Background summarization failed: {e}")

    def maybe_summarize(self, all_time_history, state):
        """
        Checks if summarization is needed. If yes, run LLM call in a background thread.
        """
        if len(all_time_history) >= 30:
            # Prevent race: Only start if not already in progress
            if not self._summary_lock.acquire(blocking=False):
                print("[DEBUG] Summarization already in progress; skipping this turn.")
                return
            
            def background_task():
                try:
                    self._background_summary_task(list(all_time_history[:30]), state)
                finally:
                    self._summary_lock.release()
            
            thread = threading.Thread(target=background_task, daemon=True)
            thread.start()
            print("[DEBUG] Started background summarization thread.")

    def prep_for_llm(self, messages, state):
        trimmed = self.smart_trim(messages)
        return self.ensure_system_message(trimmed, state)

    def invoke(self, llm, messages, config=None, state=None):
        llm_messages = self.prep_for_llm(messages, state)
        if config is None:
            return llm.invoke(llm_messages)
        return llm.invoke(llm_messages, config=config)
