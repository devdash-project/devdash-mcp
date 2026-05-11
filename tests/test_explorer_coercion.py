"""Regression tests for ``qml_explorer_set_property`` value coercion.

These guard the bug where a string ``"false"`` reached the QML side, where a
non-empty string is truthy, and so a ``bool`` property was set to *true*.

Run from the repo root:  python -m unittest discover -s tests
"""

import unittest

from src.tools import explorer
from src.tools.explorer import PropertyCoercionError, _coerce_value


class CoerceValueTests(unittest.TestCase):
    def test_string_false_to_bool_is_false(self):
        # The original bug: "false" was truthy on the QML side and set the
        # property to *true*. It must become Python False here.
        self.assertIs(_coerce_value("false", "bool"), False)

    def test_string_true_to_bool_is_true(self):
        self.assertIs(_coerce_value("true", "bool"), True)

    def test_bool_string_case_insensitive_and_trimmed(self):
        for s in ("FALSE", "False", " false ", "fAlSe"):
            self.assertIs(_coerce_value(s, "bool"), False, s)
        for s in ("TRUE", "True", " true "):
            self.assertIs(_coerce_value(s, "bool"), True, s)

    def test_real_json_bools_pass_through(self):
        self.assertIs(_coerce_value(True, "bool"), True)
        self.assertIs(_coerce_value(False, "bool"), False)

    def test_invalid_bool_string_raises(self):
        for s in ("yes", "no", "1", "0", "on", "off", "", "  "):
            with self.assertRaises(PropertyCoercionError):
                _coerce_value(s, "bool")

    def test_numeric_strings_to_int(self):
        self.assertEqual(_coerce_value("12", "int"), 12)
        self.assertIsInstance(_coerce_value("12", "int"), int)
        self.assertEqual(_coerce_value("12.0", "int"), 12)
        self.assertEqual(_coerce_value("-3", "int"), -3)
        self.assertEqual(_coerce_value(" 7 ", "int"), 7)

    def test_non_integral_int_string_raises(self):
        with self.assertRaises(PropertyCoercionError):
            _coerce_value("1.5", "int")

    def test_numeric_strings_to_real(self):
        self.assertEqual(_coerce_value("1.5", "real"), 1.5)
        self.assertEqual(_coerce_value("-45", "real"), -45.0)
        self.assertEqual(_coerce_value("0", "real"), 0.0)
        self.assertIsInstance(_coerce_value("3", "real"), float)

    def test_non_numeric_number_string_raises(self):
        for s in ("abc", "", "1,5", "px"):
            with self.assertRaises(PropertyCoercionError):
                _coerce_value(s, "real")

    def test_nan_and_inf_rejected(self):
        for s in ("nan", "inf", "-inf", "Infinity"):
            with self.assertRaises(PropertyCoercionError):
                _coerce_value(s, "real")

    def test_color_string_passthrough(self):
        for s in ("#ff0000", "red", "#aa112233"):
            self.assertEqual(_coerce_value(s, "color"), s)

    def test_enum_and_plain_string_passthrough(self):
        self.assertEqual(_coerce_value("slot", "enum"), "slot")
        self.assertEqual(_coerce_value("hello", "string"), "hello")

    def test_unknown_type_passes_string_through_unchanged(self):
        # When the declared type can't be looked up we don't guess.
        self.assertEqual(_coerce_value("true", None), "true")
        self.assertEqual(_coerce_value("12", ""), "12")

    def test_numbers_pass_through_regardless_of_declared_type(self):
        self.assertEqual(_coerce_value(5, "real"), 5)
        self.assertEqual(_coerce_value(2.5, "real"), 2.5)
        self.assertEqual(_coerce_value(0, "bool"), 0)
        self.assertEqual(_coerce_value([1, 2, 3], "vector"), [1, 2, 3])


class PropertyTypeLookupTests(unittest.TestCase):
    def _patch_send_request(self, response):
        original = explorer._send_request
        explorer._send_request = lambda request, timeout=5.0: response
        self.addCleanup(lambda: setattr(explorer, "_send_request", original))

    def test_reads_type_from_metadata(self):
        self._patch_send_request(
            {
                "success": True,
                "data": {
                    "propertyMetadata": [
                        {"name": "count", "type": "int"},
                        {"name": "hasFormShading", "type": "bool"},
                        {"name": "screwColor", "type": "color"},
                    ]
                },
            }
        )
        self.assertEqual(explorer._property_type_from_metadata("hasFormShading"), "bool")
        self.assertEqual(explorer._property_type_from_metadata("count"), "int")
        self.assertEqual(explorer._property_type_from_metadata("screwColor"), "color")
        self.assertIsNone(explorer._property_type_from_metadata("missing"))

    def test_returns_none_when_explorer_unreachable(self):
        self._patch_send_request({"success": False, "error": "connection refused"})
        self.assertIsNone(explorer._property_type_from_metadata("anything"))

    def test_returns_none_on_malformed_state(self):
        self._patch_send_request({"success": True})  # no "data"
        self.assertIsNone(explorer._property_type_from_metadata("anything"))


if __name__ == "__main__":
    unittest.main()
