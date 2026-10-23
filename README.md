# options-pricing-and-risk-desk
A real-time, distributed options desk: a live underlying feed streams onto an event bus, a Python service reprices **every strike × expiry** (prices and Greeks) on each tick, a gateway pushes binary frames to a React UI over WebSockets with **credit-based backpressure**.
