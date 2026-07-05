"""
Tests for tag normalization (tag_normalizer.py).

Focused on the British -> American spelling pass, since that's the part most
likely to regress silently: a missed word doesn't raise an error, it just lets
"defence" and "defense" become two different tag-graph nodes.
"""

from strategic_reports.daily.core.tag_normalizer import normalize_tag, normalize_tags


class TestAmericanSpelling:
    def test_standalone_word(self):
        assert normalize_tag("defence") == "defense"

    def test_within_multi_word_tag(self):
        assert normalize_tag("defence budget") == "defense budget"

    def test_case_and_hyphen_insensitive(self):
        assert normalize_tag("Cyber-Defence") == "cyber defense"

    def test_plural_form(self):
        assert normalize_tag("organisations") == "organizations"

    def test_already_american_is_unchanged(self):
        assert normalize_tag("defense") == "defense"

    def test_dedupes_british_and_american_variants(self):
        result = normalize_tags(["defence", "defense", "Colour"])
        assert result == ["defense", "color"]
