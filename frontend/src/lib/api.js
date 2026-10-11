import axios from "axios";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

export const api = axios.create({
  baseURL: API,
  timeout: 60000,
});

export const sendChat = async (message, sessionId) => {
  const { data } = await api.post("/chat", { message, session_id: sessionId });
  return data;
};

export const submitLead = async (lead) => {
  const { data } = await api.post("/leads", lead);
  return data;
};

export const submitFeedback = async (payload) => {
  const { data } = await api.post("/feedback", payload);
  return data;
};

export const adminLeads = async (token) => {
  const { data } = await api.get("/admin/leads", { headers: { "X-Admin-Token": token } });
  return data;
};

export const adminUpdateLead = async (token, leadId, patch) => {
  const { data } = await api.patch(`/admin/leads/${leadId}`, patch, {
    headers: { "X-Admin-Token": token },
  });
  return data;
};

export const adminSchedule = async (token) => {
  const { data } = await api.get("/admin/schedule", { headers: { "X-Admin-Token": token } });
  return data;
};

export const getSuggestedPrompts = async () => {
  const { data } = await api.get("/suggested-prompts");
  return data.prompts;
};

export const adminVerify = async (token) => {
  const { data } = await api.get("/admin/verify", { headers: { "X-Admin-Token": token } });
  return data;
};

export const adminStats = async (token) => {
  const { data } = await api.get("/admin/stats", { headers: { "X-Admin-Token": token } });
  return data;
};

export const adminReingest = async (token) => {
  const { data } = await api.post("/admin/reingest", {}, { headers: { "X-Admin-Token": token } });
  return data;
};

export const adminRuns = async (token) => {
  const { data } = await api.get("/admin/runs", { headers: { "X-Admin-Token": token } });
  return data.runs;
};

export const adminLogs = async (token, status) => {
  const params = status ? { status } : {};
  const { data } = await api.get("/admin/logs", {
    headers: { "X-Admin-Token": token },
    params,
  });
  return data.logs;
};

export const adminFacts = async (token) => {
  const { data } = await api.get("/admin/facts", { headers: { "X-Admin-Token": token } });
  return data.facts;
};

export const backendHealth = async () => {
  const { data } = await api.get("/health");
  return data;
};
