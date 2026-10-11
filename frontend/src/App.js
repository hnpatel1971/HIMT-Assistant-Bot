import "@/App.css";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Toaster } from "sonner";
import Embed from "@/pages/Embed";

function App() {
  return (
    <div className="App" style={{ background: "transparent" }}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Embed />} />
          <Route path="*" element={<Embed />} />
        </Routes>
      </BrowserRouter>
      <Toaster position="top-right" richColors />
    </div>
  );
}

export default App;
