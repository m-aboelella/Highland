import { WorkspaceShell } from "../components/workspace-shell";

export default function Home() {
  return (
    <WorkspaceShell>
      <section className="welcome">
        <p className="eyebrow">Discover</p>
        <h1>Find the signal across your workspace.</h1>
        <p>
          Search local enterprise knowledge, inspect its sources, and ask grounded
          follow-up questions.
        </p>
        <form className="prompt">
          <label htmlFor="question">Ask Highland</label>
          <textarea
            id="question"
            name="question"
            placeholder="Prepare me for the Northwind customer meeting…"
          />
          <button type="submit">Start discovery</button>
        </form>
      </section>
    </WorkspaceShell>
  );
}
