# License note

## In plain terms

**The big decision — keep this for-profit/closed, or open it up — is still YOURS, and it is NOT made.** I (the agent) did **not** decide it. What's on disk right now is a *recommendation only*: I put the **Apache License 2.0** in [`LICENSE`](LICENSE) as a placeholder so the repo isn't left in legal limbo, but it is **fully reversible** — nothing is published, the repo isn't even on the internet yet, and swapping or removing the file is a one-line change. **GATED-FOR-OPERATOR: your final call.**

**What a license actually does, plainly.** A license is the permission slip you staple to code so other people know what they're allowed to do with it. With **no** license, the legal default is the *opposite* of "free to use" — by default nobody else may legally copy, run, or build on your code, even if it's sitting in a public folder. So you have to pick one on purpose.

**The fork in the road (this is the part you decide):**

- **Permissive open-source (what I recommend, e.g. Apache-2.0 or MIT).** Anyone — including a competitor — may take this, use it, change it, and even sell a product built on it, for free, forever, as long as they keep your copyright notice. **Upside:** maximum adoption, contributors, goodwill, and it matches the whole framing of this project ("a founder clones this and runs it on their own company"). **Downside:** you give up the right to charge people *for the code itself*, and a competitor can fork it. You can still build a paid business *around* it (hosting, support, a managed cloud version) — that's exactly how most open-source companies make money.
- **Closed / for-profit (no open-source license, or a "source-available" license with restrictions).** You keep full control and the right to charge for the code. **Upside:** you can sell it directly and stop competitors from forking. **Downside:** far less adoption, no free contributors, and it cuts against this repo's stated purpose.

**Why I recommend Apache-2.0 over MIT** (both are permissive and both are fine — this is a mild preference, not a strong one): Apache-2.0 includes a **patent grant** — a written promise from everyone who contributes code that they won't later sue users of the project for patent infringement on that code. MIT is silent on patents. For a system like this (ingest pipelines, an LLM "genesis" engine, agent orchestration — areas where software patents exist and patent trolling happens), that explicit patent peace is worth having, and it costs you nothing. If you'd rather have the absolute shortest, most familiar license and you don't care about the patent clause, **MIT is the equally-reasonable alternative** — say the word and I'll swap it.

**What you need to decide / do (none of this is done):**
1. **The core call:** open-source vs. for-profit/closed. *(GATED-FOR-OPERATOR.)*
2. **If open-source:** confirm Apache-2.0, or tell me to switch to MIT.
3. **Fill in the copyright owner.** The Apache file's notice currently uses bracketed placeholders (`[yyyy] [name of copyright owner]`). I deliberately did **not** invent a name or a company — you need to put the real copyright holder there (you personally, or a company entity if one exists). I won't guess that.
4. **(If it matters to you)** decide whether to also add a `NOTICE` file — optional under Apache-2.0, used to carry attribution lines.

**One caveat I'm flagging, not deciding:** picking a permissive license now and going closed *later* is hard-to-impossible to fully reverse once other people have copied the code (they keep their rights to the version they got). The safe order, if you're unsure, is: **stay unpublished and undecided** (where you are now), or **go closed first** — you can always loosen to open later, but you can't easily tighten from open to closed. That asymmetry is the real reason this is a genuine gate and not a rubber-stamp.

---

*Type-2 (for-the-operator) doc per this repo's two-document-types doctrine. The agent did not cross the for-profit-vs-open-source fork; it recorded the recommendation and stopped, per the hard limits in [`CLAUDE.md`](CLAUDE.md) §4.*
