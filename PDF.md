https://github.com/arkik7/att-hackathon-task

# AT&T HACKATHON

## Efficient AI Context Service for Enterprise Engineering Knowledge

Build an efficient, AI-powered Context Service that maps connections across scattered engineering knowledge, marks source contradictions, flags security risks, fills the gaps, and provides evidence-backed insights.

**Tags:** Knowledge Graph, Multi-Modal AI, Ontology

**CONTENTS**

1. Overview
2. Technical Details
3. Implementation Hint
4. Target Deliverable for the PoC

---

## 1 Overview

### Summary

Build an efficient, AI-powered Context Service that maps connections across scattered engineering knowledge, marks source contradictions, flags security risks, fills the gaps, and provides evidence-backed insights.

### Background

In large organizations, technical knowledge is often scattered and sometimes contradictory across many disconnected sources, including GitHub repositories, wiki pages, Markdown files, architecture diagrams, API specifications, system design notes, meeting notes, deployment files, database schemas, historical documents, and incident knowledge. Some of these sources may also reveal security concerns, outdated assumptions, or hidden operational risks.

**This creates a challenge for:**

**Architects, Developers, Engineering Teams**
They need to understand, for example:

*   How systems are connected, and how a change in one service may impact another service or dependent system.
*   Which repositories are related and what they have in common.
*   Who owns a component.
*   Whether documentation is accurate and reflects the code.
*   Whether a system diagram is accurate and reflects the code and the wiki.
*   Which part of the code may be related to an application outage, based on meeting notes or incident notes.

**AI coding assistants**

AI coding assistants used by developers also need the right context at the right time. Without accurate enterprise context, generated answers or code can be incomplete, inconsistent, insecure, or incorrect.

### Examples of the Challenge

| SCENARIO | CHALLENGE | WHAT THE CONTEXT SERVICE CAN HELP WITH |
| :--- | :--- | :--- |
| **Change code in repo A** | This might impact other code somewhere else. | Define the relationship between the code repositories. |
| **Stale documentation** | A wiki page may describe an outdated database schema, while the actual schema is already there in the source code. | Detect this difference, determine which source is more current - perhaps by time, meeting notes, and other facts - and explain the inconsistency with evidence. |
| **Knowledge only in a diagram** | An architecture diagram may contain crucial information that is missing from the code. | Extract this information from the diagram, connect it to related technical sources, and represent it as evidence-backed context. |
| **Insecure patterns** | The code may contain security-related issues or insecure implementation patterns. | Detect these concerns, flag them, and help ensure that generated code does not repeat the same security risks. |

### Business Value

Context Service directly improves engineering productivity, software quality, operational reliability, and developer experience.

Briefly, the business value of this service is to:

*   Reduce engineering time spent searching for and understanding relationships, gaps, and technical context.
*   Improve the quality and security of AI-generated code by giving coding assistants better context.
*   Improve incident analysis by connecting outages to services, repositories, and code areas.
*   Provide ROI by reducing investigation time, rework, and avoidable production issues.

### Goal

**High-level conceptual view.**

The goal of this hackathon is to build an advanced agentic ETL to build the Context Service. The agentic ETL will classify, map, validate, and enrich incoming data against the predefined ontology. The challenge is to apply engineering and AI mindset to build, from scattered source information, an efficient and secure Context Service that can be used effectively by engineers and agentic AI coding assistants.

### Judging Criteria

> **Note:** Participants may have different levels of access to LLM and VLM models. Therefore, the most important judging criterion is not only the output itself, but how clearly the team explains the engineering and AI thinking behind the solution, including the design decisions, validation approach, limitations, and how the solution could be improved in the future.

| JUDGING AREA | WHAT WILL BE EVALUATED | POINTS |
| :--- | :--- | :--- |
| **Extraction Approach** | How the solution ingests and extracts knowledge from scattered engineering sources, including how the extraction is automated and the ability to handle different formats. | 15 |
| **Knowledge Representation** | How well the Knowledge Representation, including ontology, schema, and related structures, is defined, and how much it takes into consideration representing the source from multiple perspectives, such as concepts, policies, systems, relationships, dependencies, risks, and evidence. This includes how extracted knowledge is structured, validated, stored, indexed, and retrieved, for example through database-based knowledge graphs, text-based knowledge graphs, vector databases, or a hybrid approach, while keeping the context trustworthy. | 25 |
| **Context Service Connection and Usage** | How effectively the Context Service can be used by humans, IDEs, and agentic AI coding assistants, including access through MCP, APIs, or similar integration methods. | 15 |
| **Presentation and Explanation** | How clearly the team presents the engineering and AI mindset behind the solution, including the design, architecture, reasoning, trade-offs, limitations, and implementation approach. | 35 |
| **Forward View** | How convincingly the team explains how this approach could be scaled, maintained, integrated into real developer workflows, and kept accurate, secure, and trustworthy over time. | 10 |
| **Total** | | **100** |

---

## 2 Technical Details

### Enterprise Engineering Knowledge

For simplicity, the scattered information is stored in one GitHub repository that will be shared on the hackathon day.

Hackathon participants will use scattered engineering knowledge from one GitHub repository or monorepo that contains multiple technical source types, such as:

| # | SOURCE TYPE | WHAT IT MIGHT CONTAIN | HINT |
| :--- | :--- | :--- | :--- |
| **1** | **Source Code** | Python, JavaScript, including APIs, configuration files, dependency manifests, tests, and README files. | The agent can inspect actual implementations and identify dependencies. |
| **2** | **System Design Diagrams** | PNG or SVG architecture diagrams, sequence diagrams, database schemas, and service-interaction drawings. | The agent can compare the intended architecture with the actual implementation in the code. |
| **3** | **Wiki and Documentation** | Markdown pages describing APIs, business requirements, authentication, deployment, architecture decisions, and troubleshooting. | These documents explain the purpose and intended behavior of components. |
| **4** | **API Specifications** | OpenAPI YAML/JSON files, example HTTP requests and responses, event schemas, and integration contracts. | These help identify connections between services, even when their source code is located in different folders. |
| **5** | **Architecture Decisions and Meeting Notes** | Design proposals, migration plans, technical meeting summaries, architecture decision records, and change requests. | These explain why a system was designed in a certain way and may reveal relationships that are not obvious from code alone. |
| **6** | **Database Schemas and Data Models** | SQL schemas, entity-relationship diagrams, migration files, and sample data with synthetic records. | These are useful for connecting applications to shared data models and databases. |
| **7** | **Issues, Bug Reports, and Release Notes** | Exported issue descriptions, changelogs, deprecation notices, bug investigations, outage notes, and synthetic tickets. | These can reveal outdated documentation, planned integrations, production issues, and dependencies that changed over time. |
| **8** | **Dependency and Deployment Configuration** | `requirements.txt`, `package.json`, Dockerfiles, Docker Compose files, CI workflows, Terraform files, Kubernetes manifests, and environment configuration. | These help reveal shared libraries, deployment relationships, runtime dependencies, and infrastructure dependencies. |

### Example Imperfect Information in the GitHub Repository

In the proposed GitHub repository, participants may find:

*   Outdated documentation that describes an older API that is not present in the code.
*   Two diagrams that contradict each other, perhaps because one represents a previous architecture.
*   Unrelated projects that happen to use the same API. It might be fine, but it needs evidence that it is not a typo in one of these two projects.
*   Security issues in code or configuration that should not be repeated by generated code.
*   Release notes or incident reports that mention dependencies not documented anywhere else.
*   A wiki page that describes a schema differently from the current source code or migration files.

This imperfect information is intentional, but it is inspired by the reality of how enterprise repositories often look.

> The Context Service should not blindly connect everything. It should distinguish verified facts from assumptions, outdated information, contradictions, weak evidence, and security concerns.