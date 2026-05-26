# Supplier Returns — V12 Allow Extra Pieces

A supplier may physically return more items than the system shows in store 100 (miscounts, extra stock found). Remove the upper limit so staff can enter any quantity above the system number.

The minimum of 1 piece stays — you cannot send 0.

Two changes, both in the Send PO JS block inside `templates/supplier_returns/index.html`.

---

## CHANGE 1 — Remove the `max` attribute from the pieces input

In the `openPoModal` function, find the input element being built:

```javascript
'  min="1" max="' + item.systemPieces + '"' +
```

Replace with:

```javascript
'  min="1"' +
```

---

## CHANGE 2 — Remove the upper-bound check in `validatePoModal`

Find:

```javascript
  } else if (val > systemPieces) {
    invalid.push("Line " + (i + 1) + ": cannot exceed system pieces (" + systemPieces + ").");
    input.classList.add("is-invalid");
  } else {
```

Replace with:

```javascript
  } else {
```

---

That's it. The input now accepts any whole number ≥ 1. If staff enter more than the system quantity, the cases sent to PS365 will reflect the higher number — no warning, no block.
