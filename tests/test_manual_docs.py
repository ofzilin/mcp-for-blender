"""The offline manual index: RST parsing, search ranking, page rendering.

The real corpus is 2358 pages that are not vendored (they are CC-BY-SA and live
outside this package), so these tests build a tiny one with the same shapes:
overline and underline headings, a toctree, an include, a code block, an
admonition and inline roles.
"""

import pytest

from blender_mcp import manual_docs

INDEX_RST = """\
.. index:: Modeling

############
  Modeling
############

.. toctree::
   :maxdepth: 2

   modifiers/index.rst

Overview of modeling tools.
"""

MODIFIERS_INDEX = """\
Modifiers
=========

.. toctree::

   array.rst
"""

ARRAY_RST = """\
.. index:: Modeling Modifiers; Array Modifier

**************
Array Modifier
**************

The *Array* modifier duplicates geometry.

.. include:: /shared/what_modifiers_do.rst

Options
=======

Shape
   How copies are arranged.

.. code-block:: python

   obj.modifiers.new(name="Array", type="ARRAY")

Align Rotation :guilabel:`Circle`
---------------------------------

Follows the distribution direction.

.. note::
   Only for the circle shape.
"""

SHARED_RST = """\
Modifiers run in stack order, top to bottom.
"""

LIGHT_PATH_RST = """\
Light Path
==========

The Light Path node reports the kind of ray being shaded.
"""


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    root = tmp_path / "manual"
    (root / "modeling" / "modifiers").mkdir(parents=True)
    (root / "shared").mkdir()
    (root / "index.rst").write_text(INDEX_RST, encoding="utf-8")
    (root / "modeling" / "modifiers" / "index.rst").write_text(MODIFIERS_INDEX, encoding="utf-8")
    (root / "modeling" / "modifiers" / "array.rst").write_text(ARRAY_RST, encoding="utf-8")
    (root / "shared" / "what_modifiers_do.rst").write_text(SHARED_RST, encoding="utf-8")
    (root / "light_path.rst").write_text(LIGHT_PATH_RST, encoding="utf-8")

    monkeypatch.setenv(manual_docs.MANUAL_DIR_ENV, str(root))
    monkeypatch.setattr(manual_docs, "_INDEX", None)
    monkeypatch.setattr(manual_docs, "_INDEX_ROOT", None)
    return root


def test_all_pages_are_indexed(corpus):
    index = manual_docs.get_index()
    assert index is not None
    assert index.total_pages == 5


def test_search_puts_a_title_match_first(corpus):
    results = manual_docs.search("array modifier")
    first_line = results.splitlines()[2]
    assert "Array Modifier" in first_line
    assert "modeling/modifiers/array.rst" in first_line


def test_search_says_how_to_read_the_page(corpus):
    assert "get_manual_page" in manual_docs.search("light path")


def test_search_on_nothing_matching_suggests_api_lookup(corpus):
    message = manual_docs.search("zzzz no such topic")
    assert "No manual page matches" in message
    assert "bpy_api_lookup" in message


def test_page_has_a_title_body_and_subsections(corpus):
    text = manual_docs.page("modeling/modifiers/array")
    assert text.startswith("# Array Modifier")
    assert "duplicates geometry" in text
    assert "### Options" in text


def test_page_reads_included_text(corpus):
    # .. include:: /shared/what_modifiers_do.rst
    assert "stack order" in manual_docs.page("modeling/modifiers/array")


def test_page_renders_code_blocks_as_fences(corpus):
    text = manual_docs.page("modeling/modifiers/array")
    assert "```python" in text and "type=\"ARRAY\"" in text
    assert ".. code-block::" not in text


def test_page_turns_admonitions_and_roles_into_prose(corpus):
    text = manual_docs.page("modeling/modifiers/array")
    assert "Note:" in text
    assert ":guilabel:" not in text


def test_page_returns_only_the_named_section(corpus):
    text = manual_docs.page("modeling/modifiers/array", section="Options")
    assert "## Options" in text
    assert "duplicates geometry" not in text


def test_page_lists_sections_when_the_name_is_wrong(corpus):
    text = manual_docs.page("modeling/modifiers/array", section="Nope")
    assert "has no section 'Nope'" in text
    assert "Options" in text


def test_page_suggests_close_paths(corpus):
    text = manual_docs.page("modeling/modifiers/arrayy")
    assert "No manual page" in text
    assert "array.rst" in text


def test_page_can_be_found_by_title(corpus):
    text = manual_docs.page("Light Path")
    assert "kind of ray" in text


def test_long_pages_are_cut_with_a_way_to_narrow(corpus):
    page_text = """Long Page
=========

Options
=======

""" + "Copies can be scaled. " * 200
    (corpus / "long.rst").write_text(page_text, encoding="utf-8")
    text = manual_docs.page("long", max_chars=1000)
    assert "[truncated at 1000 characters" in text
    assert "section=" in text
