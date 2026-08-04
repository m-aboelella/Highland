import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type EvidenceReference = { id: string };

function linkEvidence(markdown: string, evidence: EvidenceReference[]): string {
  const known = new Set(evidence.map((item) => item.id));
  return markdown.replace(/\[(E\d+)\](?!\()/g, (marker, id: string) => (
    known.has(id) ? `[${id}](#evidence-${id})` : marker
  ));
}

export function MarkdownPreview({
  children,
  evidence = [],
}: {
  children: string;
  evidence?: EvidenceReference[];
}) {
  return (
    <article className="artifact-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children: label, href }) => href?.startsWith("#") ? (
            <a className="evidence-reference" href={href}>{label}</a>
          ) : (
            <a href={href} rel="noreferrer" target="_blank">{label}</a>
          ),
        }}
      >
        {linkEvidence(children, evidence)}
      </ReactMarkdown>
    </article>
  );
}
