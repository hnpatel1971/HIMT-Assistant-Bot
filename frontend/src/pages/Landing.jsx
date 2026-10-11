import { motion } from "framer-motion";
import { ArrowRight, Anchor, Compass, GraduationCap, Award, Ship, Waves } from "lucide-react";
import ChatWidget from "@/components/ChatWidget";

const HERO_BG = "https://images.pexels.com/photos/18415767/pexels-photo-18415767.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940";
const CARGO_IMG = "https://images.unsplash.com/photo-1613690399151-65ea69478674?crop=entropy&cs=srgb&fm=jpg&ixid=M3w3NTY2OTV8MHwxfHNlYXJjaHwxfHxvY2VhbiUyMGNhcmdvJTIwc2hpcCUyMGFlcmlhbHxlbnwwfHx8fDE3ODU1NjczNzl8MA&ixlib=rb-4.1.0&q=85";
const CONTROL_IMG = "https://images.pexels.com/photos/21753184/pexels-photo-21753184.jpeg";
const STUDENTS_IMG = "https://images.unsplash.com/photo-1534724364725-325f10a8e182";

const STATS = [
  { value: "23+", label: "Years of Excellence" },
  { value: "4", label: "Campuses" },
  { value: "90+", label: "Approved Courses" },
  { value: "3L+", label: "Students Trained" },
];

const HIGHLIGHTS = [
  {
    icon: GraduationCap,
    title: "DG Shipping Approved",
    body: "Pre-sea, post-sea, competency, revalidation and modular courses aligned with Indian and international standards.",
  },
  {
    icon: Ship,
    title: "UK MCA Approved",
    body: "Selected packages recognised by the UK Maritime & Coastguard Agency for global career mobility.",
  },
  {
    icon: Compass,
    title: "Simulator-Based Training",
    body: "Hands-on training on modern bridge, engine, and VR simulators across four campuses.",
  },
  {
    icon: Award,
    title: "Grade A1 (Outstanding)",
    body: "Consistently rated at the highest tier by DG Shipping for training quality and infrastructure.",
  },
];

export default function Landing() {
  return (
    <div data-testid="landing-page" className="min-h-screen bg-[#020813] text-slate-100">
      {/* Top nav */}
      <header className="relative z-20 border-b border-white/5">
        <div className="max-w-7xl mx-auto px-6 md:px-10 py-5 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-md bg-[#FF5722]/15 border border-[#FF5722]/40 flex items-center justify-center">
              <Anchor className="w-4 h-4 text-[#FF5722]" />
            </div>
            <div>
              <div className="font-display font-black tracking-tighter text-xl leading-none">HIMT</div>
              <div className="text-[10px] font-mono uppercase tracking-widest text-slate-400 mt-1">
                Hindustan Institute of Maritime Training
              </div>
            </div>
          </div>
          <nav className="hidden md:flex items-center gap-8 text-sm text-slate-300">
            <a href="#about" className="hover:text-white transition-colors" data-testid="nav-about">About</a>
            <a href="#programs" className="hover:text-white transition-colors" data-testid="nav-programs">Programs</a>
            <a href="#admissions" className="hover:text-white transition-colors" data-testid="nav-admissions">Admissions</a>
            <a href="#contact" className="hover:text-white transition-colors" data-testid="nav-contact">Contact</a>
          </nav>
        </div>
      </header>

      {/* Hero */}
      <section className="relative overflow-hidden grain-overlay">
        <div
          className="absolute inset-0 bg-cover bg-center"
          style={{ backgroundImage: `url(${HERO_BG})` }}
          aria-hidden
        />
        <div className="absolute inset-0 bg-gradient-to-r from-[#020813] via-[#020813]/85 to-[#020813]/40" aria-hidden />
        <div className="relative max-w-7xl mx-auto px-6 md:px-10 pt-20 pb-32 md:pt-32 md:pb-40 grid grid-cols-1 md:grid-cols-12 gap-10">
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7 }}
            className="md:col-span-8"
          >
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-[#00E5FF]/40 bg-[#00E5FF]/5 text-[#00E5FF] font-mono text-[11px] uppercase tracking-widest mb-6">
              <Waves className="w-3 h-3" /> Grade A1 · Outstanding Institute
            </div>
            <h1
              data-testid="hero-title"
              className="font-display font-black tracking-tighter text-4xl sm:text-6xl lg:text-7xl leading-[0.95]"
            >
              Where India's<br />
              <span className="text-[#00E5FF]">Maritime Careers</span><br />
              Take Anchor.
            </h1>
            <p className="mt-8 text-base md:text-lg text-slate-300 max-w-2xl leading-relaxed">
              HIMT delivers DG-Shipping and UK-MCA approved training across four campuses,
              covering competency, modular, simulator-based, and revalidation courses for
              sea-going professionals.
            </p>
            <div className="mt-10 flex flex-wrap items-center gap-4">
              <button
                data-testid="hero-cta-ask"
                onClick={() => document.querySelector("[data-testid='chat-trigger-button']")?.click()}
                className="group inline-flex items-center gap-2 px-6 py-3 rounded-full bg-[#FF5722] hover:bg-[#F4511E] text-white font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-[#00E5FF]"
              >
                Ask the HIMT Assistant
                <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
              </button>
              <a
                href="#programs"
                data-testid="hero-cta-programs"
                className="inline-flex items-center gap-2 px-6 py-3 rounded-full border border-white/20 hover:border-[#00E5FF]/60 hover:text-[#00E5FF] text-slate-200 font-medium transition-colors"
              >
                Explore Programs
              </a>
            </div>
          </motion.div>
        </div>

        {/* Stats strip */}
        <div className="relative border-t border-white/10 bg-[#020813]/70 backdrop-blur">
          <div className="max-w-7xl mx-auto px-6 md:px-10 py-8 grid grid-cols-2 md:grid-cols-4 gap-8">
            {STATS.map((s, i) => (
              <div key={i} data-testid={`hero-stat-${i}`}>
                <div className="font-display font-black text-3xl md:text-4xl text-[#FF5722] tracking-tight">
                  {s.value}
                </div>
                <div className="text-xs font-mono uppercase tracking-widest text-slate-400 mt-2">
                  {s.label}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* About */}
      <section id="about" className="max-w-7xl mx-auto px-6 md:px-10 py-24 grid grid-cols-1 md:grid-cols-12 gap-12">
        <div className="md:col-span-5 md:sticky md:top-24 self-start">
          <div className="font-mono text-xs uppercase tracking-widest text-[#00E5FF] mb-4">01 · About HIMT</div>
          <h2 className="font-display font-bold text-3xl md:text-5xl tracking-tight leading-[1.05]">
            Two decades of shaping maritime professionals.
          </h2>
        </div>
        <div className="md:col-span-7 md:col-start-7 space-y-6 text-slate-300 text-[15px] leading-relaxed">
          <p>
            HIMT — <span className="text-white font-medium">Hindustan Institute of Maritime Training</span> — has trained
            over 300,000 seafarers across pre-sea and post-sea programs since inception. Our
            programs are approved by the Directorate General of Shipping (India) and select
            packages carry UK MCA recognition.
          </p>
          <p>
            We operate four campuses with modern simulators, VR modules, and an experienced
            faculty drawn from active maritime service — bridging classroom learning with
            operational rigour.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-6 pt-2">
            {HIGHLIGHTS.map((h, i) => {
              const Icon = h.icon;
              return (
                <div
                  key={i}
                  data-testid={`highlight-${i}`}
                  className="p-6 rounded-xl border border-white/10 bg-[#0B1325]/60 hover:border-[#00E5FF]/40 transition-colors"
                >
                  <Icon className="w-5 h-5 text-[#FF5722] mb-4" />
                  <div className="font-display font-bold text-white mb-2">{h.title}</div>
                  <div className="text-sm text-slate-400 leading-relaxed">{h.body}</div>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* Programs */}
      <section id="programs" className="border-y border-white/5 bg-[#050E1F]">
        <div className="max-w-7xl mx-auto px-6 md:px-10 py-24">
          <div className="grid grid-cols-1 md:grid-cols-12 gap-10 items-end mb-14">
            <div className="md:col-span-6">
              <div className="font-mono text-xs uppercase tracking-widest text-[#00E5FF] mb-4">02 · Programs</div>
              <h2 className="font-display font-bold text-3xl md:text-5xl tracking-tight leading-[1.05]">
                Every course a seafarer needs.
              </h2>
            </div>
            <div className="md:col-span-5 md:col-start-8 text-slate-300 leading-relaxed">
              From basic modular courses to competency exams and UK MCA approved packages —
              ask the assistant for fees, duration, and eligibility for any program.
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-12 gap-6">
            <div
              className="md:col-span-7 relative rounded-2xl overflow-hidden min-h-[320px] border border-white/10"
              data-testid="program-card-modular"
            >
              <img src={CARGO_IMG} alt="Ocean cargo ship" className="absolute inset-0 w-full h-full object-cover" />
              <div className="absolute inset-0 bg-gradient-to-t from-[#020813] via-[#020813]/70 to-transparent" />
              <div className="relative h-full flex flex-col justify-end p-8">
                <div className="font-mono text-[11px] uppercase tracking-widest text-[#00E5FF] mb-2">DG Shipping Approved</div>
                <div className="font-display font-bold text-2xl md:text-3xl tracking-tight">Modular & Post-Sea Courses</div>
                <div className="text-sm text-slate-300 mt-3 max-w-lg">
                  Basic modular, advanced modular, simulator-based, revalidation and upgradation
                  courses across all four campuses.
                </div>
              </div>
            </div>
            <div
              className="md:col-span-5 relative rounded-2xl overflow-hidden min-h-[320px] border border-white/10"
              data-testid="program-card-mca"
            >
              <img src={CONTROL_IMG} alt="Ship control room" className="absolute inset-0 w-full h-full object-cover" />
              <div className="absolute inset-0 bg-gradient-to-t from-[#020813] via-[#020813]/70 to-transparent" />
              <div className="relative h-full flex flex-col justify-end p-8">
                <div className="font-mono text-[11px] uppercase tracking-widest text-[#00E5FF] mb-2">UK MCA Approved</div>
                <div className="font-display font-bold text-2xl md:text-3xl tracking-tight">MCA Packages</div>
                <div className="text-sm text-slate-300 mt-3">
                  UK MCA approved courses and combined packages for global mobility.
                </div>
              </div>
            </div>
            <div
              className="md:col-span-5 relative rounded-2xl overflow-hidden min-h-[280px] border border-white/10"
              data-testid="program-card-students"
            >
              <img src={STUDENTS_IMG} alt="Maritime students" className="absolute inset-0 w-full h-full object-cover" />
              <div className="absolute inset-0 bg-gradient-to-t from-[#020813] via-[#020813]/70 to-transparent" />
              <div className="relative h-full flex flex-col justify-end p-8">
                <div className="font-mono text-[11px] uppercase tracking-widest text-[#FF5722] mb-2">Pre-Sea</div>
                <div className="font-display font-bold text-2xl md:text-3xl tracking-tight">Foundation Programs</div>
                <div className="text-sm text-slate-300 mt-3">
                  DG Shipping approved pre-sea courses building a solid career foundation.
                </div>
              </div>
            </div>
            <div
              className="md:col-span-7 rounded-2xl p-8 border border-white/10 bg-[#020813] flex flex-col justify-between"
              data-testid="program-card-vr"
            >
              <div>
                <div className="font-mono text-[11px] uppercase tracking-widest text-[#00E5FF] mb-2">Innovation</div>
                <div className="font-display font-bold text-2xl md:text-3xl tracking-tight">
                  VR Module & Simulator Training
                </div>
                <p className="text-sm text-slate-300 mt-4 max-w-xl leading-relaxed">
                  HIMT integrates virtual reality and full-mission simulators into modular and
                  competency programs — accelerating readiness for real vessel operations.
                </p>
              </div>
              <div className="mt-8 flex flex-wrap gap-2">
                {["Bridge Sim", "Engine Sim", "VR Module", "GMDSS", "ECDIS", "ARPA"].map((tag, i) => (
                  <span
                    key={i}
                    className="text-xs font-mono px-3 py-1 rounded-full border border-white/10 text-slate-300"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Admissions */}
      <section id="admissions" className="max-w-7xl mx-auto px-6 md:px-10 py-24">
        <div className="grid grid-cols-1 md:grid-cols-12 gap-10">
          <div className="md:col-span-4">
            <div className="font-mono text-xs uppercase tracking-widest text-[#00E5FF] mb-4">03 · Admissions</div>
            <h2 className="font-display font-bold text-3xl md:text-5xl tracking-tight leading-[1.05]">
              A clean, transparent booking flow.
            </h2>
          </div>
          <div className="md:col-span-7 md:col-start-6 grid grid-cols-1 sm:grid-cols-3 gap-4">
            {[
              { n: "01", t: "Choose Course", d: "Explore DG Shipping / UK MCA approved courses that fit your career stage." },
              { n: "02", t: "Book Online", d: "Complete the online booking via HIMT's official booking procedure." },
              { n: "03", t: "Submit Documents", d: "Follow the submission procedure for verification and confirmation." },
            ].map((s, i) => (
              <div
                key={i}
                data-testid={`admission-step-${i}`}
                className="p-6 rounded-xl border border-white/10 bg-[#0B1325]/60"
              >
                <div className="font-mono text-xs text-[#FF5722] mb-3">{s.n}</div>
                <div className="font-display font-bold text-white text-lg mb-2">{s.t}</div>
                <div className="text-sm text-slate-400 leading-relaxed">{s.d}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Contact CTA */}
      <section id="contact" className="border-t border-white/5 bg-[#050E1F]">
        <div className="max-w-7xl mx-auto px-6 md:px-10 py-20 grid grid-cols-1 md:grid-cols-12 gap-10 items-center">
          <div className="md:col-span-7">
            <div className="font-mono text-xs uppercase tracking-widest text-[#00E5FF] mb-4">04 · Contact</div>
            <h2 className="font-display font-bold text-3xl md:text-5xl tracking-tight leading-[1.05]">
              Talk to the HIMT Assistant.
            </h2>
            <p className="mt-6 text-slate-300 text-base md:text-lg max-w-xl leading-relaxed">
              Ask for contact numbers, campus addresses, course fees, or booking procedures.
              The assistant answers only from official HIMT content and always cites sources.
            </p>
          </div>
          <div className="md:col-span-5 flex md:justify-end">
            <button
              data-testid="contact-cta-ask"
              onClick={() => document.querySelector("[data-testid='chat-trigger-button']")?.click()}
              className="inline-flex items-center gap-3 px-7 py-4 rounded-full bg-[#FF5722] hover:bg-[#F4511E] text-white text-base font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-[#00E5FF]"
            >
              Start a Conversation
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      </section>

      <footer className="border-t border-white/5">
        <div className="max-w-7xl mx-auto px-6 md:px-10 py-8 flex flex-col md:flex-row items-start md:items-center justify-between gap-4 text-xs font-mono text-slate-500">
          <div>© HIMT · Hindustan Institute of Maritime Training</div>
          <div className="flex items-center gap-4">
            <a href="https://himtmarine.com" target="_blank" rel="noreferrer" className="hover:text-[#00E5FF]">himtmarine.com</a>
          </div>
        </div>
      </footer>

      <ChatWidget />
    </div>
  );
}
