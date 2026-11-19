// Run: node tests/test_money.js
const assert = require("assert");
const { format, group } = require("../app/static/js/money.js");

const cases = [
  ["272641256", "RUPEE", "₹" + "27,26,41,256"],
  ["272641256.00", "RUPEE", "₹27,26,41,256"],
  ["-481634", "RUPEE", "₹(4,81,634)"],
  ["-481634.00", "LAKH", "₹(4.82)"],
  ["272641256", "LAKH", "₹2,726.41"],
  ["0", "RUPEE", "₹0"],
  ["0", "LAKH", "₹0.00"],
  ["-0.40", "RUPEE", "₹0"],            // rounds to zero: no brackets
  ["0.50", "RUPEE", "₹1"],             // half-up
  ["-0.50", "RUPEE", "₹(1)"],
  ["99999.995", "RUPEE", "₹1,00,000"],
  ["100000", "LAKH", "₹1.00"],
  ["999", "RUPEE", "₹999"],
  ["1000", "RUPEE", "₹1,000"],
  ["12345678901.00", "RUPEE", "₹12,34,56,78,901"],
];
for (const [v, unit, want] of cases) assert.strictEqual(format(v, unit), want, `${v} ${unit}`);
assert.strictEqual(group("1234567"), "12,34,567");
const { exact } = require("../app/static/js/money.js");
assert.strictEqual(exact("272641256"), "₹27,26,41,256.00");
assert.strictEqual(exact("-0.01"), "₹(0.01)");
assert.strictEqual(exact("1000.5"), "₹1,000.50");
console.log(`money.js: ${cases.length + 4} checks passed`);
