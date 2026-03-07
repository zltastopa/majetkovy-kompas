(() => {
  function readJsonScript(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    try {
      return JSON.parse(el.textContent);
    } catch {
      return null;
    }
  }

  function esc(value) {
    const el = document.createElement("span");
    el.textContent = value == null ? "" : String(value);
    return el.innerHTML;
  }

  function fmt(value) {
    return Number(value || 0).toLocaleString("sk-SK");
  }

  function fmtCurrency(value) {
    if (value == null || Number.isNaN(Number(value))) {
      return "—";
    }
    return `${fmt(value)} €`;
  }

  function asList(value) {
    return Array.isArray(value) ? value : [];
  }

  function totalIncome(data) {
    const income = data && typeof data === "object" && "income" in data ? data.income : data;
    if (!income || typeof income !== "object") {
      return 0;
    }
    return Number(income.public_function || 0) + Number(income.other || 0);
  }

  function incomeParts(data) {
    const income = data && typeof data === "object" && "income" in data ? data.income : data;
    if (!income || typeof income !== "object") {
      return { public_function: 0, other: 0 };
    }
    return {
      public_function: Number(income.public_function || 0),
      other: Number(income.other || 0),
    };
  }

  function countItems(data, key) {
    return asList(data && data[key]).length;
  }

  function lineBreak(value, emptyText = "Neuvedené") {
    if (!value) {
      return `<span class="empty-state">${esc(emptyText)}</span>`;
    }
    return esc(String(value)).replace(/\n/g, "<br>");
  }

  function entryForYear(detail, year) {
    return detail?.timeline?.find((entry) => entry.year === year) || null;
  }

  function previousYear(detail, year) {
    const years = detail?.years || [];
    const index = years.indexOf(year);
    return index > 0 ? years[index - 1] : null;
  }

  function itemSignature(item) {
    return JSON.stringify(item || null);
  }

  function renderDeltaBadge(delta, suffix = "") {
    const sign = delta > 0 ? "+" : "";
    const klass = delta > 0 ? "up" : delta < 0 ? "down" : "flat";
    return `<span class="compare-delta ${klass}">${sign}${fmt(delta)}${suffix}</span>`;
  }

  function compareSection(title, left, right, changed) {
    return `<section class="compare-section">
      <div class="compare-section-title">${esc(title)}</div>
      <div class="compare-grid">
        <div class="compare-col${changed ? " diff-old" : ""}">${left}</div>
        <div class="compare-col${changed ? " diff-new" : ""}">${right}</div>
      </div>
    </section>`;
  }

  function compareListSection(title, leftItems, rightItems, renderItem, emptyText = "Žiadne položky") {
    const leftList = asList(leftItems);
    const rightList = asList(rightItems);
    const leftKeys = new Set(leftList.map(itemSignature));
    const rightKeys = new Set(rightList.map(itemSignature));
    const changed =
      leftList.length !== rightList.length ||
      leftList.some((item) => !rightKeys.has(itemSignature(item)));

    const leftHtml =
      leftList
        .map((item) => {
          const cls = rightKeys.has(itemSignature(item)) ? "item-same" : "item-removed";
          return `<div class="${cls}">${renderItem(item)}</div>`;
        })
        .join("") || `<span class="empty-state">${esc(emptyText)}</span>`;

    const rightHtml =
      rightList
        .map((item) => {
          const cls = leftKeys.has(itemSignature(item)) ? "item-same" : "item-added";
          return `<div class="${cls}">${renderItem(item)}</div>`;
        })
        .join("") || `<span class="empty-state">${esc(emptyText)}</span>`;

    const countDelta = rightList.length - leftList.length;
    const badge =
      changed && countDelta !== 0
        ? ` ${renderDeltaBadge(countDelta, countDelta === 1 || countDelta === -1 ? " položka" : " položky")}`
        : "";

    return `<section class="compare-section">
      <div class="compare-section-title">${esc(title)}${badge}</div>
      <div class="compare-grid">
        <div class="compare-col${changed ? " diff-old" : ""}">${leftHtml}</div>
        <div class="compare-col${changed ? " diff-new" : ""}">${rightHtml}</div>
      </div>
    </section>`;
  }

  function renderPositionItem(item) {
    if (!item || typeof item !== "object") {
      return esc(item);
    }
    return `<div class="compare-item-title">${esc(item.role || "Funkcia")}</div>
      <div class="compare-item-meta">${esc(item.organization || "")}</div>`;
  }

  function renderRealEstateItem(item) {
    return `<div class="compare-item-title">${esc(item?.type || "Nehnuteľnosť")}</div>
      <div class="compare-item-meta">${[
        item?.cadastral_territory ? `Kat. územie: ${esc(item.cadastral_territory)}` : "",
        item?.lv_number ? `LV: ${esc(item.lv_number)}` : "",
        item?.share ? `Podiel: ${esc(item.share)}` : "",
      ]
        .filter(Boolean)
        .join(" · ")}</div>`;
  }

  function renderObligationItem(item) {
    return `<div class="compare-item-title">${esc(item?.type || "Záväzok")}</div>
      <div class="compare-item-meta">${[
        item?.share ? `Podiel: ${esc(item.share)}` : "",
        item?.date ? `Vznik: ${esc(item.date)}` : "",
      ]
        .filter(Boolean)
        .join(" · ")}</div>`;
  }

  function renderMovableItem(item) {
    return `<div class="compare-item-title">${esc(item?.type || "Hnuteľný majetok")}</div>
      <div class="compare-item-meta">${[
        item?.brand ? esc(item.brand) : "",
        item?.year_of_manufacture ? `(${esc(item.year_of_manufacture)})` : "",
        item?.share ? `Podiel: ${esc(item.share)}` : "",
      ]
        .filter(Boolean)
        .join(" ")}</div>`;
  }

  function renderVehicleItem(item) {
    return `<div class="compare-item-title">${esc(item?.type || "Motorové vozidlo")}</div>
      <div class="compare-item-meta">${[
        item?.brand ? esc(item.brand) : "",
        item?.year_of_manufacture ? `(${esc(item.year_of_manufacture)})` : "",
      ]
        .filter(Boolean)
        .join(" ")}</div>`;
  }

  function renderPropertyRightItem(item) {
    if (typeof item === "string") {
      return `<div class="compare-item-title">${esc(item)}</div>`;
    }
    return `<div class="compare-item-title">${esc(JSON.stringify(item || {}))}</div>`;
  }

  function renderYearSnapshotCard(label, entry) {
    const data = entry.data;
    return `<article class="compare-summary-card">
      <p class="compare-card-eyebrow">${esc(label)}</p>
      <h3>${entry.year}</h3>
      <ul class="compare-card-metrics">
        <li><span>Celkový príjem</span><strong>${fmtCurrency(totalIncome(data))}</strong></li>
        <li><span>Nehnuteľnosti</span><strong>${countItems(data, "real_estate") || "—"}</strong></li>
        <li><span>Záväzky</span><strong>${countItems(data, "obligations") || "—"}</strong></li>
      </ul>
    </article>`;
  }

  function renderYearDeltaCard(leftEntry, rightEntry) {
    const leftData = leftEntry.data;
    const rightData = rightEntry.data;
    const incomeDelta = totalIncome(rightData) - totalIncome(leftData);
    const propertyDelta = countItems(rightData, "real_estate") - countItems(leftData, "real_estate");
    const obligationDelta = countItems(rightData, "obligations") - countItems(leftData, "obligations");

    return `<article class="compare-summary-card compare-summary-card--delta">
      <p class="compare-card-eyebrow">Posun</p>
      <h3>${leftEntry.year} → ${rightEntry.year}</h3>
      <ul class="compare-card-metrics">
        <li><span>Príjem</span><strong>${renderDeltaBadge(incomeDelta, " €")}</strong></li>
        <li><span>Nehnuteľnosti</span><strong>${renderDeltaBadge(propertyDelta)}</strong></li>
        <li><span>Záväzky</span><strong>${renderDeltaBadge(obligationDelta)}</strong></li>
      </ul>
    </article>`;
  }

  function renderIncomeBlock(data, total, delta = null) {
    const parts = incomeParts(data);
    return `<div class="compare-data-block">
      <div class="compare-data-row"><span>Z verejnej funkcie</span><strong>${fmtCurrency(parts.public_function)}</strong></div>
      <div class="compare-data-row"><span>Iné</span><strong>${fmtCurrency(parts.other)}</strong></div>
      <div class="compare-data-row compare-data-row--total"><span>Celkom</span><strong>${fmtCurrency(total)}</strong></div>
      ${delta == null ? "" : `<div class="compare-data-row compare-data-row--delta"><span>Zmena</span><strong>${renderDeltaBadge(delta, " €")}</strong></div>`}
    </div>`;
  }

  function renderYearComparison(detail, leftYear, rightYear) {
    const leftEntry = entryForYear(detail, leftYear);
    const rightEntry = entryForYear(detail, rightYear);
    if (!leftEntry || !rightEntry) {
      return `<p class="empty-state">Vybrané roky sa v dátach nepodarilo nájsť.</p>`;
    }

    const leftData = leftEntry.data;
    const rightData = rightEntry.data;
    const leftTotal = totalIncome(leftData);
    const rightTotal = totalIncome(rightData);
    let html = `<div class="compare-selectors">
      <label class="compare-field">
        <span>Ľavý rok</span>
        <select data-compare-year="left">
          ${detail.years.map((year) => `<option value="${year}"${year === leftYear ? " selected" : ""}>${year}</option>`).join("")}
        </select>
      </label>
      <span class="compare-arrow">→</span>
      <label class="compare-field">
        <span>Pravý rok</span>
        <select data-compare-year="right">
          ${detail.years.map((year) => `<option value="${year}"${year === rightYear ? " selected" : ""}>${year}</option>`).join("")}
        </select>
      </label>
    </div>`;

    html += `<div class="compare-summary-grid compare-summary-grid--years">
      ${renderYearSnapshotCard("Ľavý výrez", leftEntry)}
      ${renderYearSnapshotCard("Pravý výrez", rightEntry)}
      ${renderYearDeltaCard(leftEntry, rightEntry)}
    </div>`;

    html += compareSection(
      "Príjmy",
      renderIncomeBlock(leftData, leftTotal),
      renderIncomeBlock(rightData, rightTotal, rightTotal - leftTotal),
      leftTotal !== rightTotal
    );

    html += compareSection(
      "Zamestnanie",
      lineBreak(leftData.employment, "Nevykonáva"),
      lineBreak(rightData.employment, "Nevykonáva"),
      leftData.employment !== rightData.employment
    );

    html += compareListSection("Funkcie", leftData.positions, rightData.positions, renderPositionItem, "Bez uvedených funkcií.");
    html += compareListSection("Nehnuteľnosti", leftData.real_estate, rightData.real_estate, renderRealEstateItem, "Bez uvedených nehnuteľností.");
    html += compareListSection("Záväzky", leftData.obligations, rightData.obligations, renderObligationItem, "Bez uvedených záväzkov.");

    if (asList(leftData.movable_property).length || asList(rightData.movable_property).length) {
      html += compareListSection(
        "Hnuteľný majetok",
        leftData.movable_property,
        rightData.movable_property,
        renderMovableItem,
        "Bez uvedeného hnuteľného majetku."
      );
    }

    if (asList(leftData.vehicles).length || asList(rightData.vehicles).length) {
      html += compareListSection(
        "Užívanie vozidiel",
        leftData.vehicles,
        rightData.vehicles,
        renderVehicleItem,
        "Bez uvedených vozidiel."
      );
    }

    if (asList(leftData.property_rights).length || asList(rightData.property_rights).length) {
      html += compareListSection(
        "Majetkové práva",
        leftData.property_rights,
        rightData.property_rights,
        renderPropertyRightItem,
        "Bez uvedených majetkových práv."
      );
    }

    html += compareSection(
      "Dary",
      lineBreak(leftData.gifts, "Žiadne evidované dary"),
      lineBreak(rightData.gifts, "Žiadne evidované dary"),
      leftData.gifts !== rightData.gifts
    );

    return html;
  }

  function applyPersonMode(mode) {
    document.querySelectorAll("[data-detail-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.detailPanel !== mode;
    });
    document.querySelectorAll("[data-detail-mode]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.detailMode === mode);
    });
  }

  function initPersonCompare(detail) {
    const panel = document.getElementById("person-compare-panel");
    if (!panel) {
      return;
    }

    const years = detail.years || [];
    if (years.length < 2) {
      const compareButton = document.querySelector('[data-detail-mode="compare"]');
      if (compareButton) {
        compareButton.disabled = true;
      }
      panel.innerHTML = `<p class="empty-state">Na porovnanie rokov je potrebné mať aspoň dve dostupné priznania.</p>`;
      return;
    }

    let leftYear = years[Math.max(0, years.length - 2)];
    let rightYear = years[years.length - 1];
    const compareParam = new URLSearchParams(window.location.search).get("compare");
    if (compareParam) {
      const [leftParam, rightParam] = compareParam.split(",").map((value) => Number.parseInt(value, 10));
      if (years.includes(leftParam) && years.includes(rightParam)) {
        leftYear = leftParam;
        rightYear = rightParam;
        applyPersonMode("compare");
      }
    }

    function render() {
      panel.innerHTML = renderYearComparison(detail, leftYear, rightYear);
    }

    document.querySelectorAll("[data-detail-mode]").forEach((button) => {
      button.addEventListener("click", () => {
        if (button.disabled) {
          return;
        }
        const mode = button.dataset.detailMode;
        applyPersonMode(mode);
        if (mode === "compare") {
          render();
        }
      });
    });

    document.querySelectorAll(".timeline-compare-link").forEach((button) => {
      button.addEventListener("click", () => {
        const [nextLeft, nextRight] = (button.dataset.compareYears || "")
          .split(",")
          .map((value) => Number.parseInt(value, 10));
        if (Number.isNaN(nextLeft) || Number.isNaN(nextRight)) {
          return;
        }
        leftYear = nextLeft;
        rightYear = nextRight;
        render();
        applyPersonMode("compare");
      });
    });

    panel.addEventListener("change", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLSelectElement)) {
        return;
      }
      if (target.dataset.compareYear === "left") {
        leftYear = Number.parseInt(target.value, 10);
      }
      if (target.dataset.compareYear === "right") {
        rightYear = Number.parseInt(target.value, 10);
      }
      render();
    });

    render();
  }

  function renderCompareSummaryCard(detail, year) {
    const entry = entryForYear(detail, year);
    const prev = previousYear(detail, year);
    const prevEntry = prev ? entryForYear(detail, prev) : null;
    if (!entry) {
      return `<article class="compare-summary-card">
        <p class="compare-card-eyebrow">${esc(detail.name)}</p>
        <h3>${esc(detail.role || detail.public_function || "Verejný funkcionár")}</h3>
        <p class="empty-state">Bez záznamu pre rok ${year}.</p>
        <p class="compare-card-link"><a href="../politicians/${esc(detail.slug)}/">Otvoriť detail</a></p>
      </article>`;
    }

    const data = entry.data;
    const currentTotal = totalIncome(data);
    const previousTotal = prevEntry ? totalIncome(prevEntry.data) : null;
    const delta = previousTotal == null ? null : currentTotal - previousTotal;
    const compareHref =
      prevEntry != null
        ? `../politicians/${detail.slug}/?compare=${prevEntry.year},${year}`
        : `../politicians/${detail.slug}/`;

    return `<article class="compare-summary-card">
      <p class="compare-card-eyebrow">${esc(detail.name)}</p>
      <h3>${esc(detail.role || detail.public_function || "Verejný funkcionár")}</h3>
      <ul class="compare-card-metrics">
        <li><span>Rok</span><strong>${year}</strong></li>
        <li><span>Celkový príjem</span><strong>${fmtCurrency(currentTotal)}</strong></li>
        <li><span>Posun</span><strong>${delta == null ? "Prvý dostupný rok" : renderDeltaBadge(delta, " €")}</strong></li>
        <li><span>Nehnuteľnosti</span><strong>${countItems(data, "real_estate") || "—"}</strong></li>
        <li><span>Záväzky</span><strong>${countItems(data, "obligations") || "—"}</strong></li>
      </ul>
      <p class="compare-card-link"><a href="${compareHref}">Otvoriť detail</a></p>
    </article>`;
  }

  function renderCompareDumbbellRow(detail, year, maxValue) {
    const entry = entryForYear(detail, year);
    if (!entry) {
      return `<div class="compare-dumbbell-row">
        <strong>${esc(detail.name)}</strong>
        <div class="compare-dumbbell-empty">Bez záznamu pre rok ${year}.</div>
        <span class="compare-dumbbell-values">—</span>
      </div>`;
    }

    const prev = previousYear(detail, year);
    const prevEntry = prev ? entryForYear(detail, prev) : null;
    const startValue = prevEntry ? totalIncome(prevEntry.data) : totalIncome(entry.data);
    const endValue = totalIncome(entry.data);
    const startPercent = maxValue > 0 ? (startValue / maxValue) * 100 : 0;
    const endPercent = maxValue > 0 ? (endValue / maxValue) * 100 : 0;
    const left = Math.min(startPercent, endPercent);
    const width = Math.max(Math.abs(endPercent - startPercent), 1.5);
    const delta = prevEntry ? endValue - startValue : null;

    return `<div class="compare-dumbbell-row">
      <strong>${esc(detail.name)}</strong>
      <div class="compare-dumbbell-track">
        <span class="compare-dumbbell-rail"></span>
        <span class="compare-dumbbell-line" style="left:${left}%;width:${width}%"></span>
        <span class="compare-dumbbell-dot compare-dumbbell-dot--start" style="left:${startPercent}%"></span>
        <span class="compare-dumbbell-dot compare-dumbbell-dot--end" style="left:${endPercent}%"></span>
      </div>
      <span class="compare-dumbbell-values">${prevEntry ? `${fmtCurrency(startValue)} → ${fmtCurrency(endValue)}` : fmtCurrency(endValue)}${delta == null ? "" : ` · ${renderDeltaBadge(delta, " €")}`}</span>
    </div>`;
  }

  function renderCompareTable(details, year) {
    const rows = [
      {
        label: "Funkcia",
        value: (detail) => esc(detail.role || detail.public_function || "Verejný funkcionár"),
      },
      {
        label: "Záznam pre rok",
        value: (detail) => (entryForYear(detail, year) ? String(year) : "—"),
      },
      {
        label: "Celkový príjem",
        value: (detail) => {
          const entry = entryForYear(detail, year);
          return entry ? fmtCurrency(totalIncome(entry.data)) : "—";
        },
      },
      {
        label: "Zmena oproti predchádzajúcemu roku",
        value: (detail) => {
          const entry = entryForYear(detail, year);
          const prev = previousYear(detail, year);
          const prevEntry = prev ? entryForYear(detail, prev) : null;
          if (!entry || !prevEntry) {
            return "—";
          }
          return renderDeltaBadge(totalIncome(entry.data) - totalIncome(prevEntry.data), " €");
        },
      },
      {
        label: "Z verejnej funkcie",
        value: (detail) => {
          const entry = entryForYear(detail, year);
          return entry ? fmtCurrency(incomeParts(entry.data).public_function) : "—";
        },
      },
      {
        label: "Iné príjmy",
        value: (detail) => {
          const entry = entryForYear(detail, year);
          return entry ? fmtCurrency(incomeParts(entry.data).other) : "—";
        },
      },
      {
        label: "Nehnuteľnosti",
        value: (detail) => {
          const entry = entryForYear(detail, year);
          return entry ? String(countItems(entry.data, "real_estate")) : "—";
        },
      },
      {
        label: "Záväzky",
        value: (detail) => {
          const entry = entryForYear(detail, year);
          return entry ? String(countItems(entry.data, "obligations")) : "—";
        },
      },
      {
        label: "Zaznamenané zmeny",
        value: (detail) => String(detail.total_changes || 0),
      },
      {
        label: "Pokryté roky",
        value: (detail) =>
          detail.years && detail.years.length
            ? `${detail.years[0]}–${detail.years[detail.years.length - 1]}`
            : "—",
      },
    ];

    return `<table class="compare-table">
      <thead>
        <tr>
          <th scope="col">Metrika</th>
          ${details
            .map(
              (detail) =>
                `<th scope="col"><a href="../politicians/${esc(detail.slug)}/">${esc(detail.name)}</a></th>`
            )
            .join("")}
        </tr>
      </thead>
      <tbody>
        ${rows
          .map(
            (row) => `<tr>
              <th scope="row">${esc(row.label)}</th>
              ${details.map((detail) => `<td>${row.value(detail)}</td>`).join("")}
            </tr>`
          )
          .join("")}
      </tbody>
    </table>`;
  }

  function renderComparePeopleColumns(title, details, year, getItems, renderItem, emptyText) {
    const cards = details
      .map((detail) => {
        const entry = entryForYear(detail, year);
        const items = entry ? asList(getItems(entry.data)) : [];
        const body =
          items
            .map((item) => `<li>${renderItem(item)}</li>`)
            .join("") || `<p class="empty-state">${esc(entry ? emptyText : `Bez záznamu pre rok ${year}.`)}</p>`;

        return `<article class="compare-person-card">
          <div class="compare-person-card__head">
            <h3>${esc(detail.name)}</h3>
            <span class="compare-pill">${entry ? items.length : "—"}</span>
          </div>
          <p class="compare-person-card__meta">${esc(detail.role || detail.public_function || "Verejný funkcionár")}</p>
          ${items.length ? `<ul class="compare-person-card__list">${body}</ul>` : body}
        </article>`;
      })
      .join("");

    return `<section class="compare-section">
      <div class="compare-section-title">${esc(title)}</div>
      <div class="compare-columns">${cards}</div>
    </section>`;
  }

  function renderComparePeopleText(title, details, year, getText, emptyText) {
    const cards = details
      .map((detail) => {
        const entry = entryForYear(detail, year);
        const text = entry ? getText(entry.data) : "";
        return `<article class="compare-person-card">
          <div class="compare-person-card__head">
            <h3>${esc(detail.name)}</h3>
          </div>
          <p class="compare-person-card__meta">${esc(detail.role || detail.public_function || "Verejný funkcionár")}</p>
          <div class="compare-person-card__body">${entry ? lineBreak(text, emptyText) : `<span class="empty-state">Bez záznamu pre rok ${year}.</span>`}</div>
        </article>`;
      })
      .join("");

    return `<section class="compare-section">
      <div class="compare-section-title">${esc(title)}</div>
      <div class="compare-columns">${cards}</div>
    </section>`;
  }

  async function initCompareTool() {
    const root = document.getElementById("compare-tool");
    if (!root) {
      return;
    }

    const index = readJsonScript("compare-index-data") || [];
    const meta = readJsonScript("compare-meta-data") || { years: [] };
    const slotEls = Array.from(root.querySelectorAll("[data-compare-slot]"));
    const yearEl = document.getElementById("compare-year");
    const statusEl = document.getElementById("compare-status");
    const resultsEl = document.getElementById("compare-results");
    const summaryGrid = document.getElementById("compare-summary-grid");
    const dumbbells = document.getElementById("compare-dumbbells");
    const tableWrap = document.getElementById("compare-table-wrap");
    const listsEl = document.getElementById("compare-lists");
    const jsonPrefix = root.dataset.jsonPrefix || "politicians/";
    const cache = new Map();

    const selectMarkup = [
      '<option value="">Vyberte funkcionára</option>',
      ...index.map(
        (item) =>
          `<option value="${esc(item.user_id)}">${esc(item.name)} — ${esc(
            item.role || item.public_function || "Verejný funkcionár"
          )}</option>`
      ),
    ].join("");
    slotEls.forEach((select) => {
      select.innerHTML = selectMarkup;
    });

    const years = Array.isArray(meta.years) ? [...meta.years].sort((a, b) => b - a) : [];
    yearEl.innerHTML = years.map((year) => `<option value="${year}">${year}</option>`).join("");

    const params = new URLSearchParams(window.location.search);
    const pickedIds = (params.get("ids") || "")
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean)
      .slice(0, slotEls.length);
    slotEls.forEach((select, index) => {
      select.value = pickedIds[index] || "";
    });
    if (params.get("year") && years.includes(Number.parseInt(params.get("year"), 10))) {
      yearEl.value = params.get("year");
    }

    async function loadPerson(userId) {
      if (cache.has(userId)) {
        return cache.get(userId);
      }
      const response = await fetch(`${jsonPrefix}${encodeURIComponent(userId)}.json`, {
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`Nepodarilo sa načítať ${userId}`);
      }
      const detail = await response.json();
      cache.set(userId, detail);
      return detail;
    }

    function syncUrl(ids, year) {
      const next = new URL(window.location.href);
      if (ids.length) {
        next.searchParams.set("ids", ids.join(","));
      } else {
        next.searchParams.delete("ids");
      }
      if (year) {
        next.searchParams.set("year", String(year));
      } else {
        next.searchParams.delete("year");
      }
      window.history.replaceState({}, "", next);
    }

    async function render() {
      const picked = [];
      slotEls.forEach((select) => {
        if (select.value && !picked.includes(select.value)) {
          picked.push(select.value);
        }
      });
      const year = Number.parseInt(yearEl.value, 10);
      syncUrl(picked, year);

      if (picked.length < 2) {
        resultsEl.hidden = true;
        statusEl.textContent = "Vyberte aspoň dvoch funkcionárov.";
        return;
      }

      statusEl.textContent = "Načítavam porovnanie…";

      try {
        const details = await Promise.all(picked.map(loadPerson));
        statusEl.textContent = `${details.length} funkcionári v porovnaní.`;
        resultsEl.hidden = false;

        summaryGrid.innerHTML = details.map((detail) => renderCompareSummaryCard(detail, year)).join("");

        const maxIncome = details.reduce((maxValue, detail) => {
          const entry = entryForYear(detail, year);
          const prev = previousYear(detail, year);
          const prevEntry = prev ? entryForYear(detail, prev) : null;
          return Math.max(
            maxValue,
            entry ? totalIncome(entry.data) : 0,
            prevEntry ? totalIncome(prevEntry.data) : 0
          );
        }, 0);
        dumbbells.innerHTML = details
          .map((detail) => renderCompareDumbbellRow(detail, year, maxIncome))
          .join("");

        tableWrap.innerHTML = renderCompareTable(details, year);

        listsEl.innerHTML = [
          renderComparePeopleText("Zamestnanie", details, year, (data) => data.employment, "Neuvedené."),
          renderComparePeopleColumns("Funkcie", details, year, (data) => data.positions, renderPositionItem, "Bez uvedených funkcií."),
          renderComparePeopleColumns(
            "Nehnuteľnosti",
            details,
            year,
            (data) => data.real_estate,
            renderRealEstateItem,
            "Bez uvedených nehnuteľností."
          ),
          renderComparePeopleColumns(
            "Záväzky",
            details,
            year,
            (data) => data.obligations,
            renderObligationItem,
            "Bez uvedených záväzkov."
          ),
        ].join("");
      } catch (error) {
        resultsEl.hidden = true;
        statusEl.textContent = "Porovnanie sa nepodarilo načítať.";
      }
    }

    slotEls.forEach((select) => {
      select.addEventListener("change", render);
    });
    yearEl.addEventListener("change", render);
    render();
  }

  const personData = readJsonScript("person-compare-data");
  if (personData) {
    initPersonCompare(personData);
  }

  initCompareTool();
})();
