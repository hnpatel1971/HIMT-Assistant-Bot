import { useState, useRef, useEffect } from "react";
import { MessageSquare, X, Send, Copy, Check, CheckCircle2, ThumbsUp, ThumbsDown } from "lucide-react";
import { toast } from "sonner";
import { sendChat, getSuggestedPrompts, submitLead, submitFeedback } from "@/lib/api";

const STATUS_LABEL = {
  grounded: null,
  insufficient: "Info unavailable",
  fallback: "Fallback mode",
  error: "Error",
};

// Turn markdown [label](url), bare http(s) URLs, **bold** and `*` bullets in
// an assistant answer into React nodes. Keeps line breaks. No dangerouslySet.
// URLs open in a new tab with rel=noopener.
const URL_RX = /(https?:\/\/[^\s()<>"']+)/g;
const MD_LINK_RX = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;
const BOLD_RX = /\*\*([^*]+)\*\*/g;

function renderInline(text, keyPrefix = "") {
  // Order matters: markdown links first (so their URL isn't matched twice),
  // then bare URLs, then **bold**. We split around each match into text/nodes.
  const nodes = [];
  let cursor = 0;
  const matches = [];
  let m;
  MD_LINK_RX.lastIndex = 0;
  while ((m = MD_LINK_RX.exec(text)) !== null) {
    matches.push({ type: "mdlink", start: m.index, end: m.index + m[0].length, label: m[1], url: m[2] });
  }
  URL_RX.lastIndex = 0;
  while ((m = URL_RX.exec(text)) !== null) {
    // Skip URLs already inside a markdown link range.
    const inside = matches.some(x => x.type === "mdlink" && m.index >= x.start && m.index < x.end);
    if (inside) continue;
    matches.push({ type: "url", start: m.index, end: m.index + m[0].length, url: m[0] });
  }
  BOLD_RX.lastIndex = 0;
  while ((m = BOLD_RX.exec(text)) !== null) {
    const inside = matches.some(x => m.index >= x.start && m.index < x.end);
    if (inside) continue;
    matches.push({ type: "bold", start: m.index, end: m.index + m[0].length, label: m[1] });
  }
  matches.sort((a, b) => a.start - b.start);

  matches.forEach((mt, idx) => {
    if (mt.start > cursor) nodes.push(text.slice(cursor, mt.start));
    if (mt.type === "mdlink" || mt.type === "url") {
      nodes.push(
        <a
          key={`${keyPrefix}-a-${idx}`}
          href={mt.url}
          target="_blank"
          rel="noopener noreferrer"
          data-testid="answer-link"
          className="text-blue-700 hover:text-blue-900 underline underline-offset-2 break-words"
        >
          {mt.type === "mdlink" ? mt.label : mt.url}
        </a>
      );
    } else if (mt.type === "bold") {
      nodes.push(
        <strong key={`${keyPrefix}-b-${idx}`} className="font-semibold text-gray-900">
          {mt.label}
        </strong>
      );
    }
    cursor = mt.end;
  });
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

function AnswerBody({ text }) {
  if (!text) return null;
  const lines = text.split("\n");
  return (
    <div className="leading-relaxed space-y-1">
      {lines.map((line, i) => {
        if (!line.trim()) return <div key={i} className="h-1" />;
        // Simple bullet detection: "- " or "• " or "* "
        const bulletMatch = /^[\s]*[-•*]\s+(.*)$/.exec(line);
        if (bulletMatch) {
          return (
            <div key={i} className="flex gap-2">
              <span className="text-blue-600 select-none">•</span>
              <span className="flex-1">{renderInline(bulletMatch[1], `l${i}`)}</span>
            </div>
          );
        }
        return <div key={i}>{renderInline(line, `l${i}`)}</div>;
      })}
    </div>
  );
}

export default function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState(null);
  const [prompts, setPrompts] = useState([]);
  const [leadSubmitted, setLeadSubmitted] = useState(false);
  const listRef = useRef(null);

  useEffect(() => {
    getSuggestedPrompts()
      .then(setPrompts)
      .catch(() => setPrompts([]));
  }, []);

  useEffect(() => {
    try {
      if (window.parent && window.parent !== window) {
        window.parent.postMessage(
          { source: "himt-chatbot", type: open ? "open" : "close" },
          "*"
        );
      }
    } catch (_) {}
  }, [open]);

  useEffect(() => {
    if (!listRef.current) return;
    const last = messages[messages.length - 1];
    if (loading || !last || last.role === "user") {
      listRef.current.scrollTop = listRef.current.scrollHeight;
      return;
    }
    const nodes = listRef.current.querySelectorAll("[data-testid^='message-assistant-']");
    const lastNode = nodes[nodes.length - 1];
    if (lastNode) {
      const offset = lastNode.offsetTop - listRef.current.offsetTop;
      listRef.current.scrollTo({ top: Math.max(0, offset - 8), behavior: "smooth" });
    } else {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [messages, loading]);

  const handleSend = async (textOverride) => {
    const text = (textOverride ?? input).trim();
    if (!text || loading) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }]);
    setLoading(true);
    try {
      const res = await sendChat(text, sessionId);
      if (!sessionId) setSessionId(res.session_id);
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: res.answer,
          sources: res.sources || [],
          status: res.status,
          intent: res.intent,
          logId: res.log_id,
          showLeadForm: res.offer_lead_capture && !leadSubmitted,
          sourceQuery: text,
        },
      ]);
    } catch (e) {
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: "Sorry — I couldn't reach the assistant service. Please try again in a moment.",
          sources: [],
          status: "error",
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <>
      {!open && (
        <button
          data-testid="chat-trigger-button"
          onClick={() => setOpen(true)}
          className="fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-full bg-blue-600 hover:bg-blue-700 text-white px-4 py-3 shadow-lg focus:outline-none focus:ring-2 focus:ring-blue-400"
        >
          <MessageSquare className="w-5 h-5" />
          <span className="font-medium text-sm">Chat with us</span>
        </button>
      )}

      {open && (
        <div
          data-testid="chat-panel"
          className="fixed bottom-6 right-6 z-50 w-[92vw] max-w-md h-[70vh] max-h-[600px] flex flex-col rounded-lg border border-gray-200 shadow-xl bg-white"
        >
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 bg-blue-600 rounded-t-lg">
            <div>
              <div className="font-semibold text-white text-sm leading-tight">HIMT Assistant</div>
              <div className="text-[11px] text-blue-100 mt-0.5">himtmarine.com</div>
            </div>
            <button
              data-testid="chat-close-button"
              onClick={() => setOpen(false)}
              className="p-1.5 rounded hover:bg-blue-700 text-white"
              aria-label="Close chat"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Messages */}
          <div
            ref={listRef}
            data-testid="chat-messages"
            className="flex-1 overflow-y-auto px-4 py-4 space-y-3 bg-gray-50"
          >
            {messages.length === 0 && (
              <div className="text-gray-600 text-sm">
                Hi! Ask me anything about HIMT — courses, admissions, campus, or contact details.
              </div>
            )}

            {messages.map((m, i) => (
              <div
                key={i}
                data-testid={`message-${m.role}-${i}`}
                className={m.role === "user" ? "flex justify-end" : "flex justify-start"}
              >
                <div
                  className={
                    m.role === "user"
                      ? "max-w-[85%] rounded-lg bg-blue-600 text-white px-3 py-2 text-sm"
                      : "group relative max-w-[90%] rounded-lg bg-white border border-gray-200 pl-3 pr-8 py-2.5 text-sm text-gray-800"
                  }
                >
                  {m.role === "assistant" ? (
                    <AnswerBody text={m.content} />
                  ) : (
                    <div className="whitespace-pre-wrap leading-relaxed">{m.content}</div>
                  )}

                  {m.role === "assistant" && (
                    <CopyAnswerButton
                      text={buildCopyText(m)}
                      testId={`copy-answer-${i}`}
                    />
                  )}

                  {m.role === "assistant" && m.status && STATUS_LABEL[m.status] && (
                    <div className="mt-2 text-[10px] text-amber-700 uppercase tracking-wide">
                      {STATUS_LABEL[m.status]}
                    </div>
                  )}

                  {m.role === "assistant" && m.logId && (
                    <FeedbackButtons
                      msg={m}
                      sessionId={sessionId}
                      testIdPrefix={`feedback-${i}`}
                    />
                  )}

                  {m.role === "assistant" && m.showLeadForm && (
                    <LeadCaptureForm
                      sessionId={sessionId}
                      sourceQuery={m.sourceQuery}
                      interestSeed={m.intent === "admissions" ? "Admissions enquiry" : ""}
                      onSubmitted={() => {
                        setLeadSubmitted(true);
                        toast.success("Thanks! A HIMT counsellor will reach out shortly.");
                      }}
                    />
                  )}
                </div>
              </div>
            ))}

            {loading && (
              <div className="flex justify-start" data-testid="chat-typing">
                <div className="rounded-lg bg-white border border-gray-200 px-3 py-2">
                  <span className="typing-dot" />
                  <span className="typing-dot" />
                  <span className="typing-dot" />
                </div>
              </div>
            )}
          </div>

          {/* Suggested prompts */}
          {messages.length === 0 && prompts.length > 0 && (
            <div className="px-4 py-2 flex flex-wrap gap-1.5 border-t border-gray-200 bg-white" data-testid="suggested-prompts">
              {prompts.map((p, idx) => (
                <button
                  key={idx}
                  data-testid={`suggested-prompt-${idx}`}
                  onClick={() => handleSend(p.text)}
                  className="text-xs px-2.5 py-1 rounded-full border border-gray-300 text-gray-700 hover:border-blue-500 hover:text-blue-700"
                >
                  {p.label}
                </button>
              ))}
            </div>
          )}

          {/* Input */}
          <div className="p-3 border-t border-gray-200 bg-white rounded-b-lg">
            <div className="flex items-center gap-2">
              <input
                data-testid="chat-input"
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKey}
                placeholder="Ask about HIMT courses, admissions, contact…"
                className="flex-1 bg-white border border-gray-300 rounded px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
              />
              <button
                data-testid="chat-send-button"
                onClick={() => handleSend()}
                disabled={loading || !input.trim()}
                className="p-2 rounded bg-blue-600 hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed text-white focus:outline-none focus:ring-2 focus:ring-blue-400"
                aria-label="Send"
              >
                <Send className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function LeadCaptureForm({ sessionId, sourceQuery, interestSeed, onSubmitted }) {
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [interest, setInterest] = useState(interestSeed || "");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    if (!name.trim()) return setError("Please enter your name.");
    const digits = phone.replace(/\D/g, "");
    if (digits.length < 8) return setError("Please enter a valid phone number.");
    setSubmitting(true);
    try {
      await submitLead({
        name: name.trim(),
        phone: phone.trim(),
        email: email.trim() || null,
        interest: interest.trim() || null,
        session_id: sessionId,
        source_query: sourceQuery,
      });
      setDone(true);
      onSubmitted?.();
    } catch (e) {
      setError("Could not submit. Please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  if (done) {
    return (
      <div
        data-testid="lead-form-success"
        className="mt-3 pt-3 border-t border-gray-100 flex items-center gap-1.5 text-blue-700 text-xs"
      >
        <CheckCircle2 className="w-4 h-4" />
        Thanks! A HIMT counsellor will contact you soon.
      </div>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      data-testid="lead-capture-form"
      className="mt-3 pt-3 border-t border-gray-100 space-y-2"
    >
      <div className="text-[11px] text-blue-700 font-medium mb-1">
        Want a counsellor to call you back?
      </div>
      <input
        data-testid="lead-name"
        type="text"
        placeholder="Full name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        className="w-full bg-white border border-gray-300 rounded px-2.5 py-1.5 text-xs text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
      />
      <input
        data-testid="lead-phone"
        type="tel"
        placeholder="Phone (e.g. +91 98xxxxxxxx)"
        value={phone}
        onChange={(e) => setPhone(e.target.value)}
        className="w-full bg-white border border-gray-300 rounded px-2.5 py-1.5 text-xs text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
      />
      <input
        data-testid="lead-email"
        type="email"
        placeholder="Email (optional)"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        className="w-full bg-white border border-gray-300 rounded px-2.5 py-1.5 text-xs text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
      />
      <input
        data-testid="lead-interest"
        type="text"
        placeholder="Course of interest (optional)"
        value={interest}
        onChange={(e) => setInterest(e.target.value)}
        className="w-full bg-white border border-gray-300 rounded px-2.5 py-1.5 text-xs text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
      />
      {error && (
        <div data-testid="lead-error" className="text-[11px] text-red-600">{error}</div>
      )}
      <button
        data-testid="lead-submit"
        type="submit"
        disabled={submitting}
        className="w-full py-1.5 rounded bg-blue-600 hover:bg-blue-700 disabled:opacity-40 text-white text-xs font-medium"
      >
        {submitting ? "Sending…" : "Request a callback"}
      </button>
    </form>
  );
}

function buildCopyText(msg) {
  // Flatten markdown links `[label](url)` -> `label (url)` so the copied plain
  // text stays readable when pasted into email / notes.
  const flat = (msg.content || "").replace(
    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_m, label, url) => `${label} (${url})`
  );
  return `${flat}\n\n— HIMT Assistant · himtmarine.com`;
}

function CopyAnswerButton({ text, testId }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      toast.success("Answer copied");
      setTimeout(() => setCopied(false), 1500);
    } catch (e) {
      toast.error("Could not copy.");
    }
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      data-testid={testId}
      aria-label="Copy answer"
      title="Copy answer"
      className="absolute top-1.5 right-1.5 p-1 rounded text-gray-400 hover:text-blue-600 hover:bg-gray-100"
    >
      {copied ? <Check className="w-3.5 h-3.5 text-blue-600" /> : <Copy className="w-3.5 h-3.5" />}
    </button>
  );
}


function FeedbackButtons({ msg, sessionId, testIdPrefix }) {
  const [choice, setChoice] = useState(null); // "up" | "down"

  const send = async (sentiment) => {
    if (choice) return; // already sent
    setChoice(sentiment);
    try {
      await submitFeedback({
        log_id: msg.logId,
        session_id: sessionId,
        query: msg.sourceQuery || null,
        answer_preview: (msg.content || "").slice(0, 500),
        sentiment,
        intent: msg.intent || null,
        status: msg.status || null,
      });
      toast.success(sentiment === "up" ? "Thanks for the feedback!" : "Thanks — we'll review this answer.");
    } catch (e) {
      // Silent fail — non-blocking; allow retry
      setChoice(null);
      toast.error("Could not send feedback. Please try again.");
    }
  };

  return (
    <div className="mt-2 flex items-center gap-1" data-testid={testIdPrefix}>
      <button
        type="button"
        onClick={() => send("up")}
        disabled={choice !== null}
        data-testid={`${testIdPrefix}-up`}
        aria-label="This answer was helpful"
        title="Helpful"
        className={
          "p-1 rounded transition-colors " +
          (choice === "up"
            ? "text-blue-600 bg-blue-50"
            : "text-gray-400 hover:text-blue-600 hover:bg-gray-100 disabled:opacity-40")
        }
      >
        <ThumbsUp className="w-3.5 h-3.5" />
      </button>
      <button
        type="button"
        onClick={() => send("down")}
        disabled={choice !== null}
        data-testid={`${testIdPrefix}-down`}
        aria-label="This answer was not helpful"
        title="Not helpful"
        className={
          "p-1 rounded transition-colors " +
          (choice === "down"
            ? "text-red-600 bg-red-50"
            : "text-gray-400 hover:text-red-600 hover:bg-gray-100 disabled:opacity-40")
        }
      >
        <ThumbsDown className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}
