import { useEffect, useState } from "react";
import {
  fetchWorkflows,
  createWorkflow,
  fetchWorkflowJobs,
  createJob,
  cancelJob,
  getResultDownloadUrl,
} from "./api";

function ProgressBar({ value }) {
  const safe = isNaN(value) ? 0 : Math.min(Math.max(value, 0), 1);
  return (
    <div className="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
      <div
        className="h-full bg-emerald-400 transition-all"
        style={{ width: `${Math.round(safe * 100)}%` }}
      />
    </div>
  );
}

function badgeClasses(state) {
  switch (state) {
    case "RUNNING":
      return "bg-sky-500/20 text-sky-300 border-sky-500/50";
    case "SUCCEEDED":
      return "bg-emerald-500/20 text-emerald-300 border-emerald-500/50";
    case "FAILED":
      return "bg-rose-500/20 text-rose-300 border-rose-500/50";
    case "PENDING":
      return "bg-slate-700/50 text-slate-200 border-slate-500/60";
    case "CANCELLED":
      return "bg-amber-500/20 text-amber-300 border-amber-500/50";
    default:
      return "bg-slate-700 text-slate-200 border-slate-600";
  }
}

function App() {
  const [userId, setUserId] = useState(() => {
    if (typeof window === "undefined") return "";
    return localStorage.getItem("user-id") || "";
  });

  const [workflows, setWorkflows] = useState([]);
  const [selectedWorkflow, setSelectedWorkflow] = useState(null);
  const [jobs, setJobs] = useState([]);

  const [newWorkflowName, setNewWorkflowName] = useState("");
  const [branchId, setBranchId] = useState("branch-1");
  const [jobType, setJobType] = useState("cell_segmentation");
  const [imagePath, setImagePath] = useState("/path/to/sample.svs");
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState(null);

  useEffect(() => {
    if (!userId) return;
    const interval = setInterval(() => {
      refreshData();
    }, 1500);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId, selectedWorkflow?.workflow_id]);

  const refreshData = async () => {
    if (!userId) return;
    try {
      const wf = await fetchWorkflows(userId);
      setWorkflows(wf);

      if (selectedWorkflow) {
        const updated = wf.find(
          (w) => w.workflow_id === selectedWorkflow.workflow_id
        );
        if (updated) {
          setSelectedWorkflow(updated);
          const js = await fetchWorkflowJobs(userId, updated.workflow_id);
          setJobs(js);
        }
      }
    } catch (err) {
      console.error(err);
      setErrorMsg("Failed to refresh workflows. Check backend is running.");
    }
  };

  const handleUseUserId = () => {
    if (!userId.trim()) return;
    localStorage.setItem("user-id", userId.trim());
    setErrorMsg(null);
    refreshData();
  };

  const handleCreateWorkflow = async () => {
    if (!userId || !newWorkflowName.trim()) return;
    setLoading(true);
    setErrorMsg(null);
    try {
      const wf = await createWorkflow(userId, newWorkflowName.trim());
      setNewWorkflowName("");
      setWorkflows((prev) => [...prev, wf]);
    } catch (err) {
      console.error(err);
      setErrorMsg("Failed to create workflow.");
    } finally {
      setLoading(false);
    }
  };

  const handleSelectWorkflow = async (wf) => {
    setSelectedWorkflow(wf);
    if (!userId) return;
    try {
      const js = await fetchWorkflowJobs(userId, wf.workflow_id);
      setJobs(js);
    } catch (err) {
      console.error(err);
      setErrorMsg("Failed to fetch jobs for workflow.");
    }
  };

  const handleCreateJob = async () => {
    if (!userId || !selectedWorkflow) return;
    setLoading(true);
    setErrorMsg(null);
    try {
      const job = await createJob(userId, {
        workflow_id: selectedWorkflow.workflow_id,
        branch_id: branchId.trim() || "default",
        job_type: jobType,
        image_path: imagePath.trim(),
        params: {},
      });
      setJobs((prev) => [...prev, job]);
    } catch (err) {
      console.error(err);
      setErrorMsg("Failed to enqueue job.");
    } finally {
      setLoading(false);
    }
  };

  const handleCancelJob = async (jobId) => {
    if (!userId) return;
    try {
      const updated = await cancelJob(userId, jobId);
      setJobs((prev) => prev.map((j) => (j.job_id === jobId ? updated : j)));
    } catch (err) {
      console.error(err);
      setErrorMsg("Failed to cancel job.");
    }
  };

  return (
    <div className="min-h-screen flex flex-col bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 px-6 py-4 flex items-center justify-between bg-slate-950/80 backdrop-blur">
        <div className="flex items-center gap-3">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-emerald-500/15 text-emerald-300 text-xl">
            ⚡
          </span>
          <div>
            <h1 className="text-lg font-semibold">InstanSeg Workflow Scheduler</h1>
            <p className="text-xs text-slate-400">
              Branch-aware · Multi-tenant · WSI segmentation
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <input
            className="px-3 py-1.5 text-sm rounded-lg bg-slate-900 border border-slate-700 text-slate-100 outline-none focus:ring-2 focus:ring-emerald-500/70"
            placeholder="X-User-ID (UUID or any string)"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
          />
          <button
            onClick={handleUseUserId}
            className="px-3 py-1.5 text-sm rounded-lg bg-emerald-500 text-slate-950 font-medium hover:bg-emerald-400 transition disabled:opacity-40"
            disabled={!userId.trim()}
          >
            Use ID
          </button>
        </div>
      </header>

      {errorMsg && (
        <div className="px-6 py-2 text-xs text-rose-300 bg-rose-900/30 border-b border-rose-700/60">
          {errorMsg}
        </div>
      )}

      <main className="flex-1 flex overflow-hidden">
        <section className="w-full md:w-1/3 border-r border-slate-800 p-4 flex flex-col gap-4">
          <div className="bg-slate-900/60 rounded-2xl p-4 border border-slate-800">
            <h2 className="text-sm font-semibold mb-2 text-slate-100">
              New Workflow
            </h2>
            <div className="flex flex-col gap-2">
              <input
                className="px-3 py-1.5 text-sm rounded-lg bg-slate-950 border border-slate-700 text-slate-100 outline-none focus:ring-2 focus:ring-emerald-500/70"
                placeholder="Workflow name"
                value={newWorkflowName}
                onChange={(e) => setNewWorkflowName(e.target.value)}
              />
              <button
                disabled={loading || !userId || !newWorkflowName.trim()}
                onClick={handleCreateWorkflow}
                className="px-3 py-1.5 text-sm rounded-lg bg-sky-500 text-slate-950 font-medium hover:bg-sky-400 disabled:opacity-40"
              >
                Create
              </button>
            </div>
          </div>

          <div className="flex-1 overflow-auto bg-slate-900/40 rounded-2xl p-3 border border-slate-800">
            <h2 className="text-sm font-semibold mb-3 text-slate-100">
              Workflows
            </h2>
            <div className="space-y-2">
              {workflows.map((wf) => (
                <button
                  key={wf.workflow_id}
                  onClick={() => handleSelectWorkflow(wf)}
                  className={`w-full text-left p-3 rounded-xl border transition ${
                    selectedWorkflow?.workflow_id === wf.workflow_id
                      ? "border-emerald-500/70 bg-slate-900"
                      : "border-slate-800 bg-slate-900/40 hover:bg-slate-900"
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-sm font-medium text-slate-50">
                      {wf.name}
                    </span>
                    <span
                      className={
                        "text-[10px] px-2 py-0.5 rounded-full border " +
                        badgeClasses(wf.status)
                      }
                    >
                      {wf.status}
                    </span>
                  </div>
                  <ProgressBar value={wf.progress ?? 0} />
                  <p className="mt-1 text-[11px] text-slate-400">
                    {Math.round((wf.progress ?? 0) * 100)}% complete
                  </p>
                </button>
              ))}
              {workflows.length === 0 && (
                <p className="text-xs text-slate-500">
                  No workflows yet. Create one above.
                </p>
              )}
            </div>
          </div>
        </section>

        <section className="flex-1 p-4 flex flex-col gap-4">
          {selectedWorkflow ? (
            <>
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-semibold text-slate-100">
                    Workflow: {selectedWorkflow.name}
                  </h2>
                  <p className="text-xs text-slate-400">
                    ID: {selectedWorkflow.workflow_id}
                  </p>
                </div>
              </div>

              <div className="bg-slate-900/60 rounded-2xl p-4 border border-slate-800">
                <h3 className="text-xs font-semibold mb-2 text-slate-200">
                  Add Job to this workflow
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
                  <div className="col-span-1">
                    <label className="text-[11px] text-slate-400 mb-1 block">
                      Branch ID
                    </label>
                    <input
                      className="w-full px-3 py-1.5 text-sm rounded-lg bg-slate-950 border border-slate-700 text-slate-100 outline-none focus:ring-2 focus:ring-emerald-500/70"
                      value={branchId}
                      onChange={(e) => setBranchId(e.target.value)}
                    />
                  </div>
                  <div className="col-span-1">
                    <label className="text-[11px] text-slate-400 mb-1 block">
                      Job type
                    </label>
                    <select
                      className="w-full px-3 py-1.5 text-sm rounded-lg bg-slate-950 border border-slate-700 text-slate-100 outline-none focus:ring-2 focus:ring-emerald-500/70"
                      value={jobType}
                      onChange={(e) => setJobType(e.target.value)}
                    >
                      <option value="cell_segmentation">
                        Cell segmentation
                      </option>
                      <option value="tissue_mask">Tissue mask</option>
                    </select>
                  </div>
                  <div className="col-span-1 md:col-span-2">
                    <label className="text-[11px] text-slate-400 mb-1 block">
                      Image path (SVS)
                    </label>
                    <input
                      className="w-full px-3 py-1.5 text-sm rounded-lg bg-slate-950 border border-slate-700 text-slate-100 outline-none focus:ring-2 focus:ring-emerald-500/70"
                      value={imagePath}
                      onChange={(e) => setImagePath(e.target.value)}
                    />
                  </div>
                </div>
                <div className="mt-3 flex justify-end">
                  <button
                    disabled={loading}
                    onClick={handleCreateJob}
                    className="px-4 py-1.5 text-sm rounded-lg bg-emerald-500 text-slate-950 font-medium hover:bg-emerald-400 disabled:opacity-40"
                  >
                    Enqueue job
                  </button>
                </div>
              </div>

              <div className="flex-1 overflow-auto bg-slate-900/40 rounded-2xl p-4 border border-slate-800">
                <h3 className="text-xs font-semibold mb-3 text-slate-200">
                  Jobs in this workflow
                </h3>
                <table className="w-full text-xs text-left border-collapse">
                  <thead className="text-[11px] text-slate-400 border-b border-slate-800">
                    <tr>
                      <th className="py-2 pr-2">Job ID</th>
                      <th className="py-2 pr-2">Branch</th>
                      <th className="py-2 pr-2">Type</th>
                      <th className="py-2 pr-2">State</th>
                      <th className="py-2 pr-2 w-32">Progress</th>
                      <th className="py-2 pr-2">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobs.map((job) => (
                      <tr
                        key={job.job_id}
                        className="border-b border-slate-900/60"
                      >
                        <td className="py-2 pr-2 text-[11px] text-slate-300">
                          {job.job_id.slice(0, 8)}…
                        </td>
                        <td className="py-2 pr-2 text-[11px] text-slate-200">
                          {job.branch_id}
                        </td>
                        <td className="py-2 pr-2 text-[11px] text-slate-200">
                          {job.job_type}
                        </td>
                        <td className="py-2 pr-2">
                          <span
                            className={
                              "px-2 py-0.5 rounded-full border text-[10px] " +
                              badgeClasses(job.state)
                            }
                          >
                            {job.state}
                          </span>
                        </td>
                        <td className="py-2 pr-2">
                          <ProgressBar value={job.progress ?? 0} />
                          <span className="text-[10px] text-slate-400">
                            {Math.round((job.progress ?? 0) * 100)}%
                          </span>
                        </td>
                        <td className="py-2 pr-2">
                          <div className="flex gap-2">
                            {job.state === "PENDING" && (
                              <button
                                onClick={() => handleCancelJob(job.job_id)}
                                className="px-2 py-1 text-[11px] rounded-md bg-slate-800 text-slate-200 hover:bg-slate-700"
                              >
                                Cancel
                              </button>
                            )}
                            {job.state === "SUCCEEDED" && job.result_path && (
                              <a
                                href={getResultDownloadUrl(job.job_id)}
                                target="_blank"
                                rel="noreferrer"
                                className="px-2 py-1 text-[11px] rounded-md bg-emerald-500/20 text-emerald-300 border border-emerald-400/60 hover:bg-emerald-500/30"
                              >
                                Download
                              </a>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                    {jobs.length === 0 && (
                      <tr>
                        <td
                          colSpan={6}
                          className="py-4 text-center text-[11px] text-slate-500"
                        >
                          No jobs yet. Add one above.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div className="flex flex-1 items-center justify-center text-slate-500 text-sm">
              Select a workflow on the left to view its jobs.
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

export default App;