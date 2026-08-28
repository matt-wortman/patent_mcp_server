# Patent Research Assistant — User Manual

*A plain-English guide to using the USPTO Patent MCP Server with Claude. No programming knowledge needed.*

---

## What is this?

This tool connects Claude (the AI assistant) directly to the United States Patent and Trademark Office's public databases. Once it's set up, you can ask Claude questions about patents in ordinary English — "find patents about mRNA vaccines filed since 2020" — and Claude will look up real, current government data to answer, instead of relying on what it remembers from training.

Think of it as giving Claude a library card to the USPTO. Claude does the searching, reading, and summarizing; you just ask questions.

## What can I ask for?

Here are the main things Claude can do with this tool, with example questions you can type as-is (swap in your own patent numbers and topics):

### Find patents and applications on a topic

> "Search for granted patents about CRISPR gene editing in agriculture."
>
> "Are there any published patent applications about using ultrasound to deliver drugs across the blood-brain barrier?"

Searches cover the full text of every U.S. patent, updated daily. Results are ordered by how well they match your question (most relevant first).

### Read a specific patent

> "Get the full text of patent 10,123,456 and summarize the claims in plain English."
>
> "What exactly does patent 9,876,543 cover? Give me concrete examples of what would and wouldn't infringe."

### Download a patent as a PDF

> "Download patent 10,123,456 as a PDF."

The PDF is saved to a folder on the computer running the tool (by default a folder called `patent_docs` in the system's temporary directory). Claude will tell you the exact location.

### Check the status and history of an application

> "What's the current status of application 16/123,456?"
>
> "Show me the prosecution history for application 15/987,654 — what did the examiner object to, and how did the applicant respond?"

This includes the office actions (the examiner's letters), what got rejected and why (including which legal grounds — novelty, obviousness, and so on), the back-and-forth timeline, and the documents in the official file.

### Map a patent family

> "What other applications are related to patent 10,123,456 — continuations, divisionals, parent applications?"
>
> "Does application 16/123,456 claim priority to any foreign applications?"

### Look up who owns a patent and who prosecuted it

> "Who is the current assignee of patent 10,123,456, and has it changed hands?"
>
> "Which attorneys are on record for application 16/123,456?"

### Research patent disputes

> "Has patent 10,123,456 ever been challenged at the Patent Trial and Appeal Board?"
>
> "Search for inter partes review proceedings involving Company X."
>
> "Has anyone litigated patent 10,123,456 in district court?"

Covers PTAB proceedings (the USPTO's own trial board, where patents can be challenged after grant) and a historical database of more than 74,000 district court patent lawsuits.

### Analyze citations

> "What prior art did the examiner cite against application 16/123,456, and where did each reference come from?"

### Bigger-picture research

Claude also has built-in step-by-step workflows for larger jobs. Just describe the job:

> "Do a prior art search for this invention: [describe it]."
>
> "Analyze the validity of patent 10,123,456."
>
> "Map Company X's patent portfolio."
>
> "Do a freedom-to-operate analysis for a product that [describe it]."
>
> "Map the patent landscape for solid-state batteries."

## What data does it use?

Everything comes live from the USPTO — the same public data a patent attorney or examiner would use. Four sources, in plain terms:

When reading document text, the server automatically prefers structured XML or
the USPTO's native Word file and uses PDF/OCR only when no native version is
available. It still uses the official PDF when visual layout or drawings matter.

| What it's for | Where it comes from | Needs the access key? |
|---|---|---|
| Full-text search and reading of patents and published applications; PDF downloads | USPTO Patent Public Search (updated daily) | No |
| Application status, history, ownership, patent families, official file documents | USPTO Open Data Portal | Yes |
| Patent Trial and Appeal Board challenges and decisions | USPTO PTAB database | Yes |
| Office action details, rejection analysis, citations, court litigation (historical datasets) | USPTO Data Set API | Yes |

"The access key" is a free credential from the USPTO (see setup below). Without it, topic searches and reading patents still work — the status, history, dispute, and citation features do not.

## One-time setup

If someone has already set this up for you, skip this section.

The technical steps live in the project's [README](../README.md), but in outline:

1. **A technical person installs the server** on your computer (about 10 minutes: install a small tool called `uv`, download this project, run one command).
2. **Get a free USPTO access key**: create an account at [data.uspto.gov](https://data.uspto.gov) (identity verification through ID.me is required), then click "My ODP" to see your key. This key is what unlocks the status/history/dispute features.
3. **Connect it to Claude Desktop**: a small settings-file change, described in the README under "Claude Desktop Configuration." The access key goes into that same settings entry.

After that, there's nothing to start or stop. Claude Desktop launches the tool automatically whenever it's needed. You'll know it's working if you ask Claude "check the status of the USPTO APIs" and it reports the services as reachable.

## Tips for good results

- **Use patent and application numbers when you have them.** "Patent 10,123,456" is precise; "that Stanford ultrasound patent" forces Claude to search and guess.
- **Say what form you want the answer in.** "Summarize in a table," "explain like I'm not a patent attorney," "give me a one-page memo."
- **For searches, describe the technology, not just keywords.** Claude will translate your description into the USPTO's search syntax, which handles synonyms and field-specific queries better than a bare keyword.
- **Big jobs are fine — let Claude break them up.** A portfolio analysis or landscape map involves many lookups; Claude will work through them and may take a few minutes.
- **Ask follow-ups.** Each answer is grounded in retrieved data, so "now compare claim 1 to claim 1 of the earlier patent" works naturally.

## Things to know (limits)

- **U.S. only.** This connects to the U.S. patent office. Foreign patents appear only where the USPTO records them (for example, foreign priority claims).
- **The USPTO rate-limits requests.** During heavy research Claude may pause and retry; a big job can be slow. This is normal.
- **Some data is historical.** The litigation and detailed rejection/citation datasets are periodically published snapshots, not live court feeds. Very recent lawsuits may be missing.
- **Long documents get saved to disk.** When a patent's text or a search result is too large to show in the chat, the tool saves it as a file and Claude reads it in pieces. Claude will mention when this happens.
- **Claude can still make mistakes.** The data is real, but the interpretation is Claude's. For anything with legal consequences, have a patent professional verify.
- **This is not legal advice.** It's a research assistant.

## If something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| Claude says a tool returned "403" or "Forbidden" | The USPTO access key is missing or wrong | Check step 2 of setup; make sure the key is in the Claude Desktop settings entry for this server |
| Claude says it's "rate limited" or keeps retrying | The USPTO is throttling requests | Wait a minute and ask again, or ask Claude to continue |
| A search finds nothing you expected | The query may be too narrow, or the topic uses different vocabulary in patents | Ask Claude to broaden the search or try alternative terms |
| Claude says it "could not establish a session" | The USPTO's public search site is having a moment | Try again in a few minutes |
| The patent tools don't appear at all | The server isn't connected to Claude Desktop | Re-check the "Claude Desktop Configuration" section of the README; restart Claude Desktop |

Still stuck? Ask Claude to "check the status of the USPTO APIs" — it will test each service and report which ones are reachable, which usually pinpoints the problem.

## For the curious: what's under the hood

The server offers Claude 34 individual tools grouped by data source (names like `ppubs_search_patents`, `odp_get_continuity`, `ptab_search_decisions`, `dsapi_search_rejections`). You never need to call these yourself — Claude picks the right ones — but if you're curious what exists, the full catalog is in the [README](../README.md) under "Available Tools."

*This manual describes version 0.11.1 of the USPTO Patent MCP Server.*
