"""Session state management with undo/redo for shader edits."""


class Session:
    """Tracks mutable state (shader edits) for undo/redo support."""

    def __init__(self):
        # shader_id -> list of previous code versions (stack)
        self._shader_history: dict[int, list[str]] = {}

    def push_shader_edit(self, shader_id: int, original_code: str):
        """Save the original code before a shader edit for undo."""
        if shader_id not in self._shader_history:
            self._shader_history[shader_id] = []
        self._shader_history[shader_id].append(original_code)

    def pop_shader_edit(self, shader_id: int) -> str | None:
        """Pop the last saved code for undo. Returns None if no history."""
        stack = self._shader_history.get(shader_id, [])
        if stack:
            return stack.pop()
        return None

    def clear_shader_edits(self, shader_id: int):
        """Clear edit history for a shader."""
        self._shader_history.pop(shader_id, None)

    def has_shader_edits(self, shader_id: int) -> bool:
        return bool(self._shader_history.get(shader_id))


_session: Session | None = None


def get_session() -> Session:
    """Get or create the global session instance."""
    global _session
    if _session is None:
        _session = Session()
    return _session
