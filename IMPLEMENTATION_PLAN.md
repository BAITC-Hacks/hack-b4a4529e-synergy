# Core EKT prototype

This scope replaces the earlier broad implementation plan.

## Flow

Chat UI → catalog search → choose items and quantity → chatbot asks for confirmation → local cart.

## Implementation

- Keep FastAPI and the existing static UI.
- Use downloaded EKT data and semantic embeddings; prioritize exact articles.
- Show real catalog price and stock.
- Let the chatbot prepare a selection and ask whether to add it.
- Change the cart only after the displayed confirmation button or “да, добавь”.
- Bind confirmation to that exact selection and prevent duplicate additions.
- Validate quantities and show cart items and totals.
- Keep an anonymous session across page refreshes.

## Verification

- Search returns catalog records.
- A proposal leaves the cart unchanged.
- Refusal leaves the cart unchanged.
- Confirmation adds exactly the displayed items once.
- The cart link and refresh show the same contents.

No login, accounts, admin dashboard, payment processing, order placement, or production basket integration.
