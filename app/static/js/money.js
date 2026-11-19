/* Money formatting: exact string/BigInt arithmetic, no floats.
 * Amounts arrive from the API as strings in RUPEES. Display only: Indian grouping, lakh or rupee unit,
 * negatives in brackets:  ₹27,26,41,256   ₹(4,81,634)   lakh: ₹2,726.41 / ₹(4.82)
 */
(function (root) {
  function group(intStr) {                       // 272641256 -> 27,26,41,256
    if (intStr.length <= 3) return intStr;
    let rest = intStr.slice(0, -3);
    const parts = [];
    while (rest.length > 2) { parts.unshift(rest.slice(-2)); rest = rest.slice(0, -2); }
    if (rest) parts.unshift(rest);
    return parts.join(",") + "," + intStr.slice(-3);
  }

  function toPaise(value) {                      // exact; rounds half-up to 2 decimals
    const m = /^\s*([+-])?(\d+)(?:\.(\d+))?\s*$/.exec(String(value));
    if (!m) throw new Error("Not an amount: " + value);
    const frac = m[3] || "";
    let p = BigInt(m[2]) * 100n + BigInt((frac + "00").slice(0, 2));
    if (frac.length > 2 && frac.charCodeAt(2) >= 53) p += 1n;
    return m[1] === "-" ? -p : p;
  }

  function format(value, unit) {                 // unit: "LAKH" | "RUPEE"
    if (value === null || value === undefined || value === "") return "";
    const p = toPaise(value);
    const neg = p < 0n;
    const a = neg ? -p : p;
    let body, isZero;
    if (unit === "LAKH") {
      const q = (a + 50000n) / 100000n;          // hundredths of a lakh, half-up
      isZero = q === 0n;
      body = group((q / 100n).toString()) + "." + (q % 100n).toString().padStart(2, "0");
    } else {
      const q = (a + 50n) / 100n;                // whole rupees, half-up
      isZero = q === 0n;
      body = group(q.toString());
    }
    return neg && !isZero ? "₹(" + body + ")" : "₹" + body;
  }

  function exact(value) {                        // exact to the paisa, ignores the lakh/rupee unit: ₹1,000.50  ₹(0.01)
    const p = toPaise(value), neg = p < 0n, a = neg ? -p : p;
    const body = group((a / 100n).toString()) + "." + (a % 100n).toString().padStart(2, "0");
    return neg ? "₹(" + body + ")" : "₹" + body;
  }

  const api = { format, group, toPaise, exact };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Money = api;
})(typeof window !== "undefined" ? window : globalThis);
