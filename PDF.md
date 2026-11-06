# AT&T HACKATHON
## Efektivní AI kontextová služba pro podnikové inženýrské znalosti (Efficient AI Context Service for Enterprise Engineering Knowledge)

Sestavte efektivní kontextovou službu poháněnou umělou inteligencí, která mapuje spojení napříč rozptýlenými inženýrskými znalostmi, označuje rozpory ve zdrojích, upozorňuje na bezpečnostní rizika, vyplňuje mezery a poskytuje poznatky podložené důkazy.

**Značky:** Znalostní graf, Multimodální AI, Ontologie

**OBSAH**
1. Přehled
2. Technické detaily
3. Tip pro implementaci
4. Cílový výstup pro PoC

---

## 1 Přehled

### Shrnutí
Sestavte efektivní kontextovou službu poháněnou umělou inteligencí, která mapuje spojení napříč rozptýlenými inženýrskými znalostmi, označuje rozpory ve zdrojích, upozorňuje na bezpečnostní rizika, vyplňuje mezery a poskytuje poznatky podložené důkazy.

### Pozadí
Ve velkých organizacích jsou technické znalosti často rozptýlené a někdy protichůdné napříč mnoha odpojenými zdroji, včetně repozitářů na GitHubu, wiki stránek, souborů Markdown, diagramů architektury, specifikací API, poznámek k návrhu systému, poznámek ze schůzek, souborů nasazení, schémat databází, historických dokumentů a znalostí o incidentech. Některé z těchto zdrojů mohou také odhalit bezpečnostní obavy, zastaralé předpoklady nebo skrytá provozní rizika.

**To představuje výzvu pro:**
Architekty, vývojáře a inženýrské týmy. Potřebují pochopit například:
* Jak jsou systémy propojeny a jak může změna v jedné službě ovlivnit jinou službu nebo závislý systém.
* Které repozitáře spolu souvisejí a co mají společného.
* Kdo je vlastníkem komponenty.
* Zda je dokumentace přesná a odráží kód.
* Zda je systémový diagram přesný a odráží kód i wiki.
* Která část kódu může souviset s výpadkem aplikace, na základě poznámek ze schůzek nebo poznámek k incidentům.

**Asistenti pro kódování s umělou inteligencí**
Asistenti pro kódování s umělou inteligencí, které používají vývojáři, také potřebují správný kontext ve správný čas. Bez přesného podnikového kontextu mohou být generované odpovědi nebo kód neúplné, nekonzistentní, nezabezpečené nebo nesprávné.

### Příklady výzvy

| SCÉNÁŘ | VÝZVA | S ČÍM MŮŽE KONTEXTOVÁ SLUŽBA POMOCI |
| :--- | :--- | :--- |
| **Změna kódu v repozitáři A** | To může ovlivnit jiný kód někde jinde. | Definovat vztah mezi repozitáři kódu. |
| **Zastaralá dokumentace** | Wiki stránka může popisovat zastaralé schéma databáze, zatímco skutečné schéma už je ve zdrojovém kódu. | Detekovat tento rozdíl, určit, který zdroj je aktuálnější (možná podle času, poznámek ze schůzek a dalších faktů) a vysvětlit nesrovnalost pomocí důkazů. |
| **Znalosti pouze v diagramu** | Diagram architektury může obsahovat klíčové informace, které v kódu chybí. | Extrahovat tyto informace z diagramu, propojit je s relevantními technickými zdroji a reprezentovat je jako kontext podložený důkazy. |
| **Nezabezpečené vzory** | Kód může obsahovat problémy související s bezpečností nebo nezabezpečené implementační vzory. | Odhalit tyto obavy, označit je a pomoci zajistit, aby generovaný kód neopakoval stejná bezpečnostní rizika. |

### Obchodní hodnota
Kontextová služba přímo zlepšuje inženýrskou produktivitu, kvalitu softwaru, provozní spolehlivost a vývojářskou zkušenost. Stručně řečeno, obchodní hodnota této služby je:
* Snížit inženýrský čas strávený hledáním a chápáním vztahů, mezer a technického kontextu.
* Zlepšit kvalitu a bezpečnost kódu generovaného umělou inteligencí tím, že asistentům pro kódování poskytne lepší kontext.
* Zlepšit analýzu incidentů propojením výpadků se službami, repozitáři a oblastmi kódu.
* Poskytnout návratnost investic (ROI) zkrácením doby vyšetřování, přepracování a vyhnutelných produkčních problémů.

### Cíl
Koncepční pohled na vysoké úrovni.
Cílem tohoto hackathonu je vybudovat pokročilé agentní ETL k vytvoření kontextové služby. Agentní ETL bude klasifikovat, mapovat, ověřovat a obohacovat příchozí data vůči předdefinované ontologii. Výzvou je aplikovat inženýrské myšlení a AI k vybudování efektivní a bezpečné kontextové služby z rozptýlených zdrojových informací, kterou mohou efektivně využívat inženýři a agentní AI kódovací asistenti.

### Kritéria hodnocení

> **Poznámka:** Účastníci mohou mít různé úrovně přístupu k modelům LLM a VLM. Proto nejdůležitějším kritériem hodnocení není jen samotný výstup, ale jak jasně tým vysvětlí inženýrské a AI myšlení za řešením, včetně rozhodnutí o návrhu, přístupu k validaci, omezení a jak by se řešení dalo v budoucnu zlepšit.

| OBLAST HODNOCENÍ | CO SE BUDE HODNOTIT | BODY |
| :--- | :--- | :--- |
| **Přístup k extrakci** | Jak řešení přijímá a extrahuje znalosti z rozptýlených inženýrských zdrojů, včetně toho, jak je extrakce automatizována a schopnosti zpracovat různé formáty. | 15 |
| **Reprezentace znalostí** | Jak dobře je definována reprezentace znalostí, včetně ontologie, schématu a souvisejících struktur, a jak moc bere v úvahu reprezentaci zdroje z více perspektiv, jako jsou koncepty, politiky, systémy, vztahy, závislosti, rizika a důkazy. To zahrnuje, jak jsou extrahované znalosti strukturovány, validovány, ukládány, indexovány a vyhledávány (např. přes databázové znalostní grafy, textové znalostní grafy, vektorové databáze nebo hybridní přístup) při zachování důvěryhodnosti kontextu. | 25 |
| **Připojení a využití kontextové služby** | Jak efektivně mohou kontextovou službu používat lidé, IDE a agentní kódovací AI asistenti, včetně přístupu přes MCP, API nebo podobné integrační metody. | 15 |
| **Prezentace a vysvětlení** | Jak jasně tým prezentuje inženýrské a AI myšlení za řešením, včetně návrhu, architektury, uvažování, kompromisů, omezení a implementačního přístupu. | 35 |
| **Pohled vpřed** | Jak přesvědčivě tým vysvětlí, jak by tento přístup mohl být škálován, udržován, integrován do reálných pracovních postupů vývojářů a udržován přesný, bezpečný a důvěryhodný v průběhu času. | 10 |
| **Celkem** | | **100** |

---

## 2 Technické detaily

### Podnikové inženýrské znalosti
Pro jednoduchost jsou rozptýlené informace uloženy v jednom repozitáři na GitHubu, který bude sdílen v den hackathonu. Účastníci hackathonu budou využívat rozptýlené inženýrské znalosti z jednoho repozitáře na GitHubu nebo monorepa, které obsahuje více technických typů zdrojů, jako jsou:

| # | TYP ZDROJE | CO MŮŽE OBSAHOVAT | NÁPOVĚDA |
| :--- | :--- | :--- | :--- |
| **1** | **Zdrojový kód** | Python, JavaScript, včetně API, konfiguračních souborů, manifestů závislostí, testů a souborů README. | Agent může prozkoumat skutečné implementace a identifikovat závislosti. |
| **2** | **Diagramy návrhu systému** | PNG nebo SVG diagramy architektury, sekvenční diagramy, databázová schémata a nákresy interakcí služeb. | Agent může porovnat zamýšlenou architekturu se skutečnou implementací v kódu. |
| **3** | **Wiki a dokumentace** | Markdown stránky popisující API, obchodní požadavky, autentizaci, nasazení, architektonická rozhodnutí a řešení problémů. | Tyto dokumenty vysvětlují účel a zamýšlené chování komponent. |
| **4** | **Specifikace API** | OpenAPI YAML/JSON soubory, příklady HTTP požadavků a odpovědí, schémata událostí a integrační kontrakty. | Pomáhají identifikovat spojení mezi službami, i když je jejich zdrojový kód umístěn v různých složkách. |
| **5** | **Rozhodnutí o architektuře a poznámky ze schůzek** | Návrhy designu, plány migrace, technická shrnutí schůzek, záznamy o rozhodnutích o architektuře a požadavky na změny. | Vysvětlují, proč byl systém navržen určitým způsobem, a mohou odhalit vztahy, které nejsou zjevné pouze z kódu. |
| **6** | **Databázová schémata a datové modely** | SQL schémata, diagramy entit a vztahů, migrační soubory a vzorová data se syntetickými záznamy. | Jsou užitečné pro propojení aplikací se sdílenými datovými modely a databázemi. |
| **7** | **Problémy, hlášení o chybách a poznámky k vydání** | Exportované popisy problémů, protokoly změn, oznámení o ukončení podpory, vyšetřování chyb, poznámky k výpadkům a syntetické tikety. | Mohou odhalit zastaralou dokumentaci, plánované integrace, produkční problémy a závislosti, které se časem změnily. |
| **8** | **Konfigurace závislostí a nasazení** | `requirements.txt`, `package.json`, Dockerfiles, Docker Compose soubory, CI workflows, Terraform soubory, Kubernetes manifesty a konfigurace prostředí. | Pomáhají odhalit sdílené knihovny, vztahy nasazení, běhové závislosti a závislosti infrastruktury. |

### Příklad nedokonalých informací v repozitáři na GitHubu
V navrženém repozitáři na GitHubu mohou účastníci najít:
* Zastaralou dokumentaci, která popisuje starší API, jež se v kódu nenachází.
* Dva diagramy, které si navzájem odporují, možná proto, že jeden představuje předchozí architekturu.
* Nesouvisející projekty, které náhodou používají stejné API. (U nichž je třeba doložit, že se nejedná o překlep v jednom z projektů).
* Bezpečnostní problémy v kódu nebo konfiguraci, které by generovaný kód neměl opakovat.
* Poznámky k vydání nebo zprávy o incidentech, které zmiňují nikde nedokumentované závislosti.
* Wiki stránku, která popisuje schéma jinak než aktuální zdrojový kód nebo migrační soubory.

Tyto nedokonalé informace jsou úmyslné, ale inspirují se realitou toho, jak podnikové repozitáře často vypadají.

> Kontextová služba by neměla slepě propojovat vše. Měla by odlišovat ověřená fakta od předpokladů, zastaralých informací, rozporů, slabých důkazů a bezpečnostních obav.