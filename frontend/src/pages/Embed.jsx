import { useEffect } from "react";
import ChatWidget from "@/components/ChatWidget";

export default function Embed() {
  useEffect(() => {
    // Transparent background so this page can be embedded as a floating iframe on himtmarine.com
    document.documentElement.style.background = "transparent";
    document.body.style.background = "transparent";
  }, []);

  return (
    <div
      data-testid="embed-shell"
      style={{ background: "transparent", minHeight: "100vh" }}
    >
      <ChatWidget />
    </div>
  );
}
