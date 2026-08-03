"use client";

import { useState } from "react";

const customers = [
  { label: "Northwind Bank", value: "cus_northwind" },
  { label: "Alpine Outfitters", value: "cus_alpine" },
  { label: "Lumon Health", value: "cus_lumon" },
];

const sourceTypes = [
  { label: "Runbooks", value: "runbook" },
  { label: "Support tickets", value: "ticket" },
  { label: "Incidents", value: "incident" },
  { label: "Meeting notes", value: "meeting_notes" },
  { label: "Messages", value: "message" },
  { label: "Project issues", value: "issue" },
  { label: "Architecture documents", value: "architecture" },
  { label: "Customer plans", value: "customer-plan" },
  { label: "Policies", value: "policy" },
  { label: "Release notes", value: "release-notes" },
];

export function SourceFilterControls() {
  const [customer, setCustomer] = useState("");
  const [sourceType, setSourceType] = useState("");
  const customerLabel = customers.find((item) => item.value === customer)?.label;
  const sourceLabel = sourceTypes.find((item) => item.value === sourceType)?.label;
  const summary = [customerLabel ?? "All customers", sourceLabel ?? "All source types"].join(" · ");

  return (
    <details className="source-filters">
      <summary>
        <span>Limit sources <small>Optional</small></span>
        <b>{summary}</b>
      </summary>
      <div className="source-filter-fields">
        <label htmlFor="customer-filter">
          Customer
          <select
            id="customer-filter"
            name="customer_id"
            onChange={(event) => setCustomer(event.target.value)}
            value={customer}
          >
            <option value="">All customers</option>
            {customers.map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </select>
        </label>
        <label htmlFor="source-type-filter">
          Source type
          <select
            id="source-type-filter"
            name="source_type"
            onChange={(event) => setSourceType(event.target.value)}
            value={sourceType}
          >
            <option value="">All source types</option>
            {sourceTypes.map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </select>
        </label>
        <p>
          Filters narrow which indexed passages the model can see. Source visibility is enforced
          automatically by the workspace and does not need to be configured here.
        </p>
      </div>
    </details>
  );
}
