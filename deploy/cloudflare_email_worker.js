/**
 * Roobico — Cloudflare Email Worker: relays the location inbox mail
 * (orders-<token>@roobico.com) to the app webhook as a raw RFC 822 message.
 *
 * Setup: see deploy/email_orders_inbox.md.
 * Bindings (Worker → Settings → Variables):
 *   INBOUND_URL     = https://app.roobico.com/inbound/email
 *   INBOUND_SECRET  = same value as INBOUND_EMAIL_WEBHOOK_SECRET in the app .env (mark as secret)
 *
 * Email Routing rule: catch-all (or "orders-*") → Send to a Worker → this worker.
 * Explicit rules (billing@, workorders@ → gmail) keep precedence over catch-all.
 */
export default {
  async email(message, env, ctx) {
    const url = env.INBOUND_URL || "https://app.roobico.com/inbound/email";
    const secret = env.INBOUND_SECRET || "";
    if (!secret) {
      console.error("INBOUND_SECRET is not configured");
      message.setReject("Inbox temporarily unavailable");
      return;
    }

    const to = (message.to || "").toLowerCase();
    if (!/^orders-[a-z0-9]{8,32}@/.test(to)) {
      // Not a location inbox address: let it bounce so senders learn the address is wrong.
      message.setReject("Unknown mailbox");
      return;
    }

    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "message/rfc822",
        "X-Inbound-Secret": secret,
        "X-Envelope-From": message.from || "",
        "X-Envelope-To": to,
      },
      body: message.raw,
    });

    if (res.status === 413) {
      // Message too large for the app (>16 MB raw). Nothing to retry.
      console.warn("inbound: message too large", to, message.headers.get("subject"));
      return;
    }
    if (!res.ok) {
      // 5xx → reject so Cloudflare/sender retries later; 4xx → drop.
      const body = await res.text().catch(() => "");
      console.error("inbound: webhook failed", res.status, body.slice(0, 200));
      if (res.status >= 500) {
        message.setReject("Temporary failure, please retry");
      }
    }
  },
};
