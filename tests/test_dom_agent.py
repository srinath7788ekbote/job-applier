"""
tests/test_dom_agent.py
Unit tests for the DOM-aware form filling agent.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import dom_agent


# ---------------------------------------------------------------------------
# Test: _format_element_for_prompt
# ---------------------------------------------------------------------------

class TestFormatElement:
    def test_basic_input(self):
        el = {
            "tag": "input", "type": "text", "name": "first_name",
            "label": "First Name", "placeholder": "Enter first name",
            "value": "", "required": True, "readonly": False,
            "options": None, "text": "", "checked": False,
        }
        line = dom_agent._format_element_for_prompt(0, el)
        assert "[0]" in line
        assert '<input type="text"' in line
        assert 'label="First Name"' in line
        assert "required" in line
        assert 'value=""' in line

    def test_select_with_options(self):
        el = {
            "tag": "select", "type": "", "name": "country",
            "label": "Country", "placeholder": "", "value": "--Select--",
            "required": False, "readonly": False,
            "options": [
                {"value": "", "text": "--Select--", "selected": True},
                {"value": "ae", "text": "UAE", "selected": False},
                {"value": "in", "text": "India", "selected": False},
            ],
            "text": "", "checked": False,
        }
        line = dom_agent._format_element_for_prompt(3, el)
        assert "[3]" in line
        assert "<select" in line
        assert "UAE" in line
        assert 'selected="--Select--"' in line

    def test_button_with_text(self):
        el = {
            "tag": "button", "type": "submit", "name": "",
            "label": "", "placeholder": "", "value": "",
            "required": False, "readonly": False,
            "options": None, "text": "Submit Application", "checked": False,
        }
        line = dom_agent._format_element_for_prompt(5, el)
        assert "[5]" in line
        assert '"Submit Application"' in line

    def test_checkbox(self):
        el = {
            "tag": "input", "type": "checkbox", "name": "agree",
            "label": "I agree to terms", "placeholder": "", "value": "",
            "required": True, "readonly": False,
            "options": None, "text": "", "checked": False,
        }
        line = dom_agent._format_element_for_prompt(2, el)
        assert "checked=False" in line

    def test_file_input(self):
        el = {
            "tag": "input", "type": "file", "name": "resume",
            "label": "Upload Resume", "placeholder": "", "value": "",
            "required": True, "readonly": False,
            "options": None, "text": "", "checked": False,
        }
        line = dom_agent._format_element_for_prompt(4, el)
        assert 'type="file"' in line
        assert "Upload Resume" in line


# ---------------------------------------------------------------------------
# Test: build_form_prompt
# ---------------------------------------------------------------------------

class TestBuildFormPrompt:
    def setup_method(self):
        self.profile = {
            "full_name": "Srinath Ekbote",
            "email": "srinath@example.com",
            "phone": "+971-50-1234567",
            "location": "Dubai, UAE",
            "current_title": "Senior DevOps Engineer",
            "years_of_experience": "8",
        }
        self.elements = [
            {"tag": "input", "type": "text", "name": "first_name",
             "label": "First Name", "placeholder": "", "value": "",
             "required": True, "readonly": False, "options": None,
             "text": "", "checked": False},
            {"tag": "input", "type": "email", "name": "email",
             "label": "Email", "placeholder": "", "value": "",
             "required": True, "readonly": False, "options": None,
             "text": "", "checked": False},
        ]

    def test_prompt_contains_profile(self):
        prompt = dom_agent.build_form_prompt(
            self.elements, self.profile, "/tmp/resume.pdf",
            "https://example.com/apply", "Apply Now", [], 0
        )
        assert "Srinath Ekbote" in prompt
        assert "srinath@example.com" in prompt
        assert "+971-50-1234567" in prompt

    def test_prompt_contains_elements(self):
        prompt = dom_agent.build_form_prompt(
            self.elements, self.profile, "/tmp/resume.pdf",
            "https://example.com/apply", "Apply Now", [], 0
        )
        assert "[0]" in prompt
        assert "[1]" in prompt
        assert "First Name" in prompt
        assert "Email" in prompt

    def test_prompt_contains_errors(self):
        errors = ["Email is required", "Phone format invalid"]
        prompt = dom_agent.build_form_prompt(
            self.elements, self.profile, "/tmp/resume.pdf",
            "https://example.com/apply", "Apply Now", errors, 1
        )
        assert "VALIDATION ERRORS" in prompt
        assert "Email is required" in prompt
        assert "Phone format invalid" in prompt

    def test_prompt_contains_step_info(self):
        prompt = dom_agent.build_form_prompt(
            self.elements, self.profile, "/tmp/resume.pdf",
            "https://example.com/apply", "Apply Now", [], 2, max_steps=8
        )
        assert "STEP: 3 of 8" in prompt

    def test_prompt_contains_action_schema(self):
        prompt = dom_agent.build_form_prompt(
            self.elements, self.profile, "/tmp/resume.pdf",
            "https://example.com/apply", "Apply Now", [], 0
        )
        assert '"action": "fill"' in prompt
        assert '"action": "select"' in prompt
        assert '"action": "upload"' in prompt
        assert '"action": "click"' in prompt
        assert '"action": "done"' in prompt

    def test_prompt_name_splitting(self):
        prompt = dom_agent.build_form_prompt(
            self.elements, self.profile, "/tmp/resume.pdf",
            "https://example.com/apply", "Apply Now", [], 0
        )
        assert "Srinath" in prompt  # first name
        assert "Ekbote" in prompt   # last name

    def test_prompt_location_splitting(self):
        prompt = dom_agent.build_form_prompt(
            self.elements, self.profile, "/tmp/resume.pdf",
            "https://example.com/apply", "Apply Now", [], 0
        )
        assert "Dubai" in prompt
        assert "UAE" in prompt


# ---------------------------------------------------------------------------
# Test: compute_state_signature
# ---------------------------------------------------------------------------

class TestStateSignature:
    def test_same_state_same_hash(self):
        elements = [
            {"tag": "input", "name": "email", "label": "Email", "value": ""},
            {"tag": "button", "name": "", "label": "", "value": ""},
        ]
        h1 = dom_agent.compute_state_signature("https://example.com", elements)
        h2 = dom_agent.compute_state_signature("https://example.com", elements)
        assert h1 == h2

    def test_different_url_different_hash(self):
        elements = [{"tag": "input", "name": "email", "label": "Email", "value": ""}]
        h1 = dom_agent.compute_state_signature("https://example.com/step1", elements)
        h2 = dom_agent.compute_state_signature("https://example.com/step2", elements)
        assert h1 != h2

    def test_value_change_changes_hash(self):
        e1 = [{"tag": "input", "name": "email", "label": "Email", "value": ""}]
        e2 = [{"tag": "input", "name": "email", "label": "Email", "value": "test@test.com"}]
        h1 = dom_agent.compute_state_signature("https://example.com", e1)
        h2 = dom_agent.compute_state_signature("https://example.com", e2)
        assert h1 != h2

    def test_element_count_change_changes_hash(self):
        e1 = [{"tag": "input", "name": "a", "label": "", "value": ""}]
        e2 = [
            {"tag": "input", "name": "a", "label": "", "value": ""},
            {"tag": "input", "name": "b", "label": "", "value": ""},
        ]
        h1 = dom_agent.compute_state_signature("https://example.com", e1)
        h2 = dom_agent.compute_state_signature("https://example.com", e2)
        assert h1 != h2


# ---------------------------------------------------------------------------
# Test: execute_action
# ---------------------------------------------------------------------------

class TestExecuteAction:
    def _make_element(self, tag="input", type_="text"):
        handle = MagicMock()
        handle.click = MagicMock()
        handle.type = MagicMock()
        handle.evaluate = MagicMock()
        handle.select_option = MagicMock()
        handle.set_input_files = MagicMock()
        handle.check = MagicMock()
        handle.scroll_into_view_if_needed = MagicMock()
        return {
            "tag": tag, "type": type_, "name": "field",
            "label": "Field", "_handle": handle,
            "options": [
                {"value": "us", "text": "USA", "selected": False},
                {"value": "ae", "text": "UAE", "selected": False},
            ] if tag == "select" else None,
        }

    @patch("dom_agent._human_delay", lambda *a, **kw: None)
    def test_fill_action(self):
        page = MagicMock()
        el = self._make_element()
        elements = [el]
        action = {"action": "fill", "index": 0, "value": "John"}
        
        result = dom_agent.execute_action(page, elements, action, "/tmp/resume.pdf")
        assert result is True
        el["_handle"].click.assert_called_once()
        el["_handle"].type.assert_called_once_with("John", delay=30)

    @patch("dom_agent._human_delay", lambda *a, **kw: None)
    def test_select_action_exact_match(self):
        page = MagicMock()
        el = self._make_element(tag="select")
        elements = [el]
        action = {"action": "select", "index": 0, "value": "UAE"}
        
        result = dom_agent.execute_action(page, elements, action, "/tmp/resume.pdf")
        assert result is True
        el["_handle"].select_option.assert_called_once_with(value="ae")

    @patch("dom_agent._human_delay", lambda *a, **kw: None)
    def test_select_action_fuzzy_match(self):
        page = MagicMock()
        el = self._make_element(tag="select")
        elements = [el]
        action = {"action": "select", "index": 0, "value": "usa"}
        
        result = dom_agent.execute_action(page, elements, action, "/tmp/resume.pdf")
        assert result is True
        el["_handle"].select_option.assert_called_once_with(value="us")

    @patch("dom_agent._human_delay", lambda *a, **kw: None)
    def test_upload_action(self):
        page = MagicMock()
        el = self._make_element(type_="file")
        elements = [el]
        action = {"action": "upload", "index": 0}
        
        result = dom_agent.execute_action(page, elements, action, "/tmp/resume.pdf")
        assert result is True
        el["_handle"].set_input_files.assert_called_once_with("/tmp/resume.pdf")

    @patch("dom_agent._human_delay", lambda *a, **kw: None)
    def test_click_action(self):
        page = MagicMock()
        el = self._make_element(tag="button", type_="submit")
        elements = [el]
        action = {"action": "click", "index": 0}
        
        result = dom_agent.execute_action(page, elements, action, "/tmp/resume.pdf")
        assert result is True
        el["_handle"].scroll_into_view_if_needed.assert_called_once()
        el["_handle"].click.assert_called_once()

    @patch("dom_agent._human_delay", lambda *a, **kw: None)
    def test_check_action(self):
        page = MagicMock()
        el = self._make_element(type_="checkbox")
        elements = [el]
        action = {"action": "check", "index": 0}
        
        result = dom_agent.execute_action(page, elements, action, "/tmp/resume.pdf")
        assert result is True
        el["_handle"].check.assert_called_once()

    @patch("dom_agent._human_delay", lambda *a, **kw: None)
    def test_invalid_index(self):
        page = MagicMock()
        elements = [self._make_element()]
        action = {"action": "fill", "index": 5, "value": "x"}
        
        result = dom_agent.execute_action(page, elements, action, "/tmp/resume.pdf")
        assert result is False

    @patch("dom_agent._human_delay", lambda *a, **kw: None)
    def test_done_action(self):
        page = MagicMock()
        elements = [self._make_element()]
        action = {"action": "done", "success": True, "reason": ""}
        
        result = dom_agent.execute_action(page, elements, action, "/tmp/resume.pdf")
        assert result is True


# ---------------------------------------------------------------------------
# Test: extract_validation_errors
# ---------------------------------------------------------------------------

class TestExtractValidationErrors:
    def test_extracts_visible_errors(self):
        page = MagicMock()
        mock_el1 = MagicMock()
        mock_el1.is_visible.return_value = True
        mock_el1.text_content.return_value = "Email is required"
        
        mock_el2 = MagicMock()
        mock_el2.is_visible.return_value = True
        mock_el2.text_content.return_value = "Phone must include country code"
        
        mock_el3 = MagicMock()
        mock_el3.is_visible.return_value = False  # hidden
        mock_el3.text_content.return_value = "Hidden error"
        
        page.query_selector_all.return_value = [mock_el1, mock_el2, mock_el3]
        
        errors = dom_agent.extract_validation_errors(page)
        assert len(errors) == 2
        assert "Email is required" in errors
        assert "Phone must include country code" in errors

    def test_no_duplicates(self):
        page = MagicMock()
        mock_el1 = MagicMock()
        mock_el1.is_visible.return_value = True
        mock_el1.text_content.return_value = "Required field"
        
        mock_el2 = MagicMock()
        mock_el2.is_visible.return_value = True
        mock_el2.text_content.return_value = "Required field"
        
        page.query_selector_all.return_value = [mock_el1, mock_el2]
        
        errors = dom_agent.extract_validation_errors(page)
        assert len(errors) == 1

    def test_empty_text_skipped(self):
        page = MagicMock()
        mock_el = MagicMock()
        mock_el.is_visible.return_value = True
        mock_el.text_content.return_value = "   "
        
        page.query_selector_all.return_value = [mock_el]
        
        errors = dom_agent.extract_validation_errors(page)
        assert len(errors) == 0


# ---------------------------------------------------------------------------
# Test: dom_agent_fill (integration with mocks)
# ---------------------------------------------------------------------------

class TestDomAgentFill:
    @patch("dom_agent._human_delay", lambda *a, **kw: None)
    @patch("dom_agent.call_llm")
    @patch("dom_agent.extract_dom_state")
    def test_done_success_on_first_step(self, mock_extract, mock_llm):
        page = MagicMock()
        page.url = "https://example.com/apply"
        page.title.return_value = "Apply"
        page.screenshot = MagicMock()
        page.inner_text.return_value = ""
        page.query_selector_all.return_value = []
        
        mock_extract.return_value = [
            {"tag": "input", "type": "text", "name": "name", "label": "Name",
             "value": "Srinath", "placeholder": "", "required": False, "readonly": False,
             "options": None, "text": "", "checked": False, "_handle": MagicMock()},
        ]
        
        mock_llm.return_value = json.dumps([
            {"action": "done", "success": True, "reason": "Form already filled"}
        ])
        
        profile = {"full_name": "Srinath Ekbote", "email": "s@e.com"}
        result = dom_agent.dom_agent_fill(page, "https://example.com/apply",
                                          "/tmp/resume.pdf", profile)
        
        assert result["success"] is True
        assert result["method"] == "dom_agent"

    @patch("dom_agent._human_delay", lambda *a, **kw: None)
    @patch("dom_agent.call_llm")
    @patch("dom_agent.extract_dom_state")
    def test_no_elements_returns_failure(self, mock_extract, mock_llm):
        page = MagicMock()
        page.url = "https://example.com/apply"
        page.title.return_value = "Apply"
        page.screenshot = MagicMock()
        page.query_selector_all.return_value = []
        
        mock_extract.return_value = []
        
        profile = {"full_name": "Test User"}
        result = dom_agent.dom_agent_fill(page, "https://example.com/apply",
                                          "/tmp/resume.pdf", profile)
        
        assert result["success"] is False
        assert "No interactive elements" in result["error"]

    @patch("dom_agent._human_delay", lambda *a, **kw: None)
    @patch("dom_agent.call_llm")
    @patch("dom_agent.extract_dom_state")
    def test_stuck_detection(self, mock_extract, mock_llm):
        page = MagicMock()
        page.url = "https://example.com/apply"
        page.title.return_value = "Apply"
        page.screenshot = MagicMock()
        page.inner_text.return_value = ""
        page.query_selector_all.return_value = []
        
        # Return same elements every time (stuck)
        mock_extract.return_value = [
            {"tag": "input", "type": "text", "name": "email", "label": "Email",
             "value": "", "placeholder": "", "required": True, "readonly": False,
             "options": None, "text": "", "checked": False, "_handle": MagicMock()},
        ]
        
        # LLM tries to fill but element doesn't change (mock doesn't update value)
        mock_llm.return_value = json.dumps([
            {"action": "fill", "index": 0, "value": "test@test.com"}
        ])
        
        profile = {"full_name": "Test", "email": "test@test.com"}
        result = dom_agent.dom_agent_fill(page, "https://example.com/apply",
                                          "/tmp/resume.pdf", profile, max_steps=5)
        
        # After 3 iterations with same state, should detect stuck
        assert result["success"] is False
        assert "stuck" in result["error"].lower()

    @patch("dom_agent._human_delay", lambda *a, **kw: None)
    @patch("dom_agent.call_llm")
    @patch("dom_agent.extract_dom_state")
    def test_success_page_detection(self, mock_extract, mock_llm):
        page = MagicMock()
        page.url = "https://example.com/apply"
        page.title.return_value = "Apply"
        page.screenshot = MagicMock()
        page.query_selector_all.return_value = []
        
        call_count = [0]
        
        def fake_extract(p):
            call_count[0] += 1
            if call_count[0] == 1:
                return [
                    {"tag": "button", "type": "submit", "name": "", "label": "",
                     "value": "", "placeholder": "", "required": False, "readonly": False,
                     "options": None, "text": "Submit", "checked": False,
                     "_handle": MagicMock()},
                ]
            # After submit, return different elements (confirmation page)
            return [
                {"tag": "button", "type": "button", "name": "", "label": "",
                 "value": "", "placeholder": "", "required": False, "readonly": False,
                 "options": None, "text": "Back to Home", "checked": False,
                 "_handle": MagicMock()},
            ]
        
        mock_extract.side_effect = fake_extract
        
        # First call: click submit, second call would see success page
        mock_llm.return_value = json.dumps([{"action": "click", "index": 0}])
        
        # After executing click, page text shows confirmation
        page.inner_text.return_value = "Thank you for applying! We'll be in touch."
        
        profile = {"full_name": "Test"}
        result = dom_agent.dom_agent_fill(page, "https://example.com/apply",
                                          "/tmp/resume.pdf", profile)
        
        assert result["success"] is True
