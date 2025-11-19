import axios from "axios";

const API_BASE = "http://localhost:8000";

export async function fetchWorkflows(userId) {
  const res = await axios.get(`${API_BASE}/workflows`, {
    headers: { "X-User-ID": userId },
  });
  return res.data;
}

export async function createWorkflow(userId, name) {
  const res = await axios.post(
    `${API_BASE}/workflows`,
    { name },
    { headers: { "X-User-ID": userId } }
  );
  return res.data;
}

export async function fetchWorkflowJobs(userId, workflowId) {
  const res = await axios.get(
    `${API_BASE}/workflows/${workflowId}/jobs`,
    { headers: { "X-User-ID": userId } }
  );
  return res.data;
}

export async function createJob(userId, data) {
  const res = await axios.post(`${API_BASE}/jobs`, data, {
    headers: { "X-User-ID": userId },
  });
  return res.data;
}

export async function cancelJob(userId, jobId) {
  const res = await axios.post(`${API_BASE}/jobs/${jobId}/cancel`, null, {
    headers: { "X-User-ID": userId },
  });
  return res.data;
}

export function getResultDownloadUrl(jobId) {
  return `${API_BASE}/jobs/${jobId}/result`;
}