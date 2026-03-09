let currentDetail = null;
const SK_MEDIAN_SOURCE =
  "https://hn24.hnonline.sk/hn24/96189316-desatina-pracujucich-vlani-zarabala-menej-ako-tisic-eur-mesacne";

function initDetail() {
  const dataEl = document.getElementById("person-detail-data");
  if (!dataEl) {
    return;
  }

  currentDetail = JSON.parse(dataEl.textContent);
  const detail = currentDetail;

  const nameEl = document.getElementById("detail-name");
  const functionEl = document.getElementById("detail-function");
  const sourceEl = document.getElementById("detail-source");
  if (!nameEl || !functionEl || !sourceEl) {
    return;
  }

  nameEl.textContent = detail.name || "";
  functionEl.textContent = detail.public_function || "";
  sourceEl.innerHTML = renderSourceLinks(detail);

  renderYearTabs(detail);
  renderDetailContext(detail);
  renderYearData(detail, detail.years[detail.years.length - 1]);
  renderIncomeChart(detail);
  renderTimeline(detail);
}

function renderSourceLinks(detail) {
  const extraction = detail.latest_extraction || {};
  const parts = [
    `<a href="https://www.nrsr.sk/web/Default.aspx?sid=vnf/oznamenie&UserId=${detail.user_id}" target="_blank" rel="noreferrer">→ Originál na nrsr.sk</a>`,
  ];

  if (extraction.file_url) {
    parts.push(`<a href="${esc(extraction.file_url)}" target="_blank" rel="noreferrer">YAML na GitHube</a>`);
  }

  if (extraction.compare_url) {
    parts.push(`<a href="${esc(extraction.compare_url)}" target="_blank" rel="noreferrer">Diff poslednej extrakcie</a>`);
  } else if (extraction.commit_url) {
    parts.push(`<a href="${esc(extraction.commit_url)}" target="_blank" rel="noreferrer">Commit poslednej extrakcie</a>`);
  }

  let html = parts.join(" · ");
  if (!extraction.committed_at) {
    return html;
  }

  const diff = extraction.diff || {};
  let summary = `Posledná extrakcia: ${esc(extraction.committed_at)}. `;
  if (diff.type === "changed" && Array.isArray(extraction.summary) && extraction.summary.length) {
    summary += `Zachytené zmeny: ${extraction.summary.slice(0, 3).map(esc).join(" · ")}`;
    if (extraction.summary.length > 3) {
      summary += ` · a ďalšie ${extraction.summary.length - 3}`;
    }
  } else if (diff.type === "new") {
    summary += "Priznanie pribudlo v poslednej extrakcii.";
  } else if (diff.type === "removed") {
    summary += "Priznanie v poslednej extrakcii zmizlo.";
  } else {
    summary += "Bez zmeny v poslednej extrakcii.";
  }

  return `${html}<div class="ctx-rank">${summary}</div>`;
}

function renderDetailContext(detail) {
  const el = document.getElementById("detail-context");
  if (!el) {
    return;
  }

  const context = detail.context || {};
  const income = Number(context.income || 0);
  const properties = Number(context.properties || 0);
  const obligations = Number(context.obligations || 0);
  const incomePercentile = Number(context.income_percentile || 0);
  const propertyPercentile = Number(context.property_percentile || 0);
  const medianIncome = Number(context.median_income || 0);
  const medianProperties = Number(context.median_properties || 0);
  const slovakMedian = Number(context.slovak_median_income || 0);
  const slovakMedianMonthly = slovakMedian > 0 ? Math.round(slovakMedian / 12) : 0;
  const multiple = Number(context.slovak_income_multiple || 0);
  const latestYear = context.latest_year || detail.years[detail.years.length - 1];
  const incomeDeltaFromMedian = income > 0 && medianIncome > 0 ? income - medianIncome : 0;

  el.innerHTML = `<div class="context-card">
    <div class="ctx-item">
      <div class="ctx-val">${income > 0 ? `${fmt(income)} €` : "—"}</div>
      <div class="ctx-label">celkový príjem (${latestYear})</div>
      ${
        income > 0
          ? `<div class="ctx-rank">vyšší ako u ${incomePercentile}% funkcionárov · medián funkcionárov: ${fmt(medianIncome)} €</div>
             ${progressBar(incomePercentile, incomePercentile >= 90 ? "high" : "")}`
          : `<div class="ctx-rank">Bez uvedeného príjmu v poslednom priznaní.</div>`
      }
      ${
        multiple >= 1.5
          ? `<div class="ctx-rank">${multiple.toFixed(1)}× <a href="${SK_MEDIAN_SOURCE}" target="_blank" rel="noreferrer">ročný medián príjmu na Slovensku</a> (${fmt(slovakMedian)} €)</div>`
          : ""
      }
    </div>
    <div class="ctx-item ctx-item--median">
      <div class="ctx-val">${medianIncome > 0 ? `${fmt(medianIncome)} €` : "—"}</div>
      <div class="ctx-label">medián funkcionárov</div>
      ${
        income > 0 && medianIncome > 0
          ? `<div class="ctx-rank">${fmt(Math.abs(incomeDeltaFromMedian))} € ${incomeDeltaFromMedian >= 0 ? "nad" : "pod"} mediánom</div>`
          : `<div class="ctx-rank">Referenčná hodnota pre porovnanie aktuálneho príjmu.</div>`
      }
      ${
        slovakMedian > 0
          ? `<div class="ctx-rank">Zdroj SR: <a href="${SK_MEDIAN_SOURCE}" target="_blank" rel="noreferrer">${fmt(slovakMedianMonthly)} € / mes. v ${latestYear}</a> · ${fmt(slovakMedian)} € ročne</div>`
          : ""
      }
    </div>
    <div class="ctx-item">
      <div class="ctx-val">${properties || "—"}</div>
      <div class="ctx-label">nehnuteľností</div>
      ${
        properties > 0
          ? `<div class="ctx-rank">viac ako ${propertyPercentile}% funkcionárov · medián: ${medianProperties}</div>
             ${progressBar(propertyPercentile, propertyPercentile >= 90 ? "high" : "")}`
          : `<div class="ctx-rank">Bez evidovaných nehnuteľností v poslednom priznaní.</div>`
      }
    </div>
    <div class="ctx-item">
      <div class="ctx-val">${obligations || "—"}</div>
      <div class="ctx-label">záväzkov</div>
      <div class="ctx-rank">Počet úverov, hypoték a ďalších záväzkov v poslednom priznaní.</div>
    </div>
    <div class="ctx-item">
      <div class="ctx-val">${detail.total_changes || "—"}</div>
      <div class="ctx-label">zmien za ${detail.years.length} rokov</div>
      <div class="ctx-rank">Súčet zachytených zmien medzi po sebe idúcimi priznaniami.</div>
    </div>
  </div>`;
}

function renderYearTabs(detail) {
  const el = document.getElementById("year-tabs");
  if (!el) {
    return;
  }

  const latest = detail.years[detail.years.length - 1];
  el.innerHTML = detail.years
    .map(
      (year) =>
        `<button onclick="renderYearData(currentDetail, ${year})" class="${year === latest ? "active" : ""}">${year}</button>`,
    )
    .join("");
}

function renderYearData(detail, year) {
  document.querySelectorAll(".year-tabs button").forEach((button) => {
    button.classList.toggle("active", Number(button.textContent) === year);
  });

  const entry = detail.timeline.find((item) => item.year === year);
  if (!entry) {
    return;
  }

  const data = entry.data;
  const diff = entry.diff;
  const changedMap = {};
  if (diff && diff.changes) {
    diff.changes.forEach((change) => {
      changedMap[change.field] = change;
    });
  }

  let html = "";

  html += section("Príjmy", () => {
    if (!data.income) {
      return empty();
    }

    const incomeChange = changedMap.income;
    let publicHtml = `${fmt(data.income.public_function || 0)} €`;
    let otherHtml = `${fmt(data.income.other || 0)} €`;
    if (incomeChange && incomeChange.old && typeof incomeChange.old === "object") {
      const oldPublic = incomeChange.old.public_function || 0;
      const oldOther = incomeChange.old.other || 0;
      if (oldPublic !== (data.income.public_function || 0)) {
        publicHtml = `<span class="data-old">${fmt(oldPublic)} €</span> → <strong>${fmt(data.income.public_function || 0)} €</strong>`;
      }
      if (oldOther !== (data.income.other || 0)) {
        otherHtml = `<span class="data-old">${fmt(oldOther)} €</span> → <strong>${fmt(data.income.other || 0)} €</strong>`;
      }
    }

    return row("Z verejnej funkcie", publicHtml) + row("Iné", otherHtml);
  });

  html += section("Zamestnanie", () =>
    data.employment ? `<div>${esc(data.employment).replace(/\n/g, "<br>")}</div>` : empty("nevykonáva"),
  );

  html += section("Funkcie", () => {
    if (!data.positions || !data.positions.length) {
      return empty("žiadne");
    }
    return itemsList(
      data.positions,
      (position) => `
      <div class="item-role">${esc(position.role)}</div>
      ${position.organization ? `<div class="item-detail">${esc(position.organization)}</div>` : ""}
      ${
        position.benefits && position.benefits !== "ŽIADNE"
          ? `<div class="item-detail">Požitky: ${esc(position.benefits)}</div>`
          : ""
      }`,
    );
  });

  html += section("Nehnuteľnosti", () => {
    if (!data.real_estate) {
      return empty("nevlastní");
    }
    return itemsList(
      data.real_estate,
      (estate) => `
      <div class="item-role">${esc(estate.type)}</div>
      <div class="item-detail">${[
        estate.cadastral_territory ? `Kat. územie: ${esc(estate.cadastral_territory)}` : "",
        estate.lv_number ? `LV: ${esc(estate.lv_number)}` : "",
        estate.share ? `Podiel: ${esc(estate.share)}` : "",
      ]
        .filter(Boolean)
        .join(" · ")}</div>`,
    );
  });

  html += section("Hnuteľný majetok", () => {
    if (!data.movable_property) {
      return empty("nevlastní");
    }
    if (typeof data.movable_property === "string") {
      return esc(data.movable_property);
    }
    return itemsList(
      data.movable_property,
      (item) => `
      <div class="item-role">${esc(item.type || "")}</div>
      <div class="item-detail">${[
        item.brand,
        item.year_of_manufacture ? `(${item.year_of_manufacture})` : "",
        item.share ? `Podiel: ${item.share}` : "",
      ]
        .filter(Boolean)
        .join(" ")}</div>`,
    );
  });

  if (data.vehicles) {
    html += section("Užívanie vozidiel", () =>
      itemsList(
        data.vehicles,
        (vehicle) => `
        <div class="item-role">${esc(vehicle.type || "MOTOROVÉ VOZIDLO")}</div>
        <div class="item-detail">${[
          vehicle.brand,
          vehicle.year_of_manufacture ? `(${vehicle.year_of_manufacture})` : "",
        ]
          .filter(Boolean)
          .join(" ")}</div>`,
      ),
    );
  }

  html += section("Záväzky", () => {
    if (!data.obligations) {
      return empty("žiadne");
    }
    return itemsList(
      data.obligations,
      (obligation) => `
      <div class="item-role">${esc(obligation.type)}</div>
      <div class="item-detail">${[
        obligation.share ? `Podiel: ${esc(obligation.share)}` : "",
        obligation.date ? `Vznik: ${esc(obligation.date)}` : "",
      ]
        .filter(Boolean)
        .join(" · ")}</div>`,
    );
  });

  if (data.property_rights) {
    html += section("Majetkové práva", () =>
      itemsList(data.property_rights, (right) =>
        typeof right === "string" ? esc(right) : esc(JSON.stringify(right)),
      ),
    );
  }

  html += section("Dary", () => (data.gifts ? esc(data.gifts) : empty("žiadne")));

  const detailContent = document.getElementById("detail-content");
  if (detailContent) {
    detailContent.innerHTML = html;
  }
}

function renderIncomeChart(detail) {
  const el = document.getElementById("income-chart");
  if (!el) {
    return;
  }

  const incomes = detail.timeline.map((item) => ({
    year: item.year,
    pub: item.data.income ? item.data.income.public_function || 0 : 0,
    other: item.data.income ? item.data.income.other || 0 : 0,
  }));
  const maxIncome = Math.max(...incomes.map((item) => item.pub + item.other), 1);

  el.innerHTML = incomes
    .map((item) => {
      const total = item.pub + item.other;
      const totalPct = (total / maxIncome) * 100;
      const otherShare = total > 0 ? (item.other / total) * 100 : 0;
      const barHeight = Math.max(totalPct, 3);
      return `<div class="income-bar-group">
      <div class="income-bar-value">${fmt(total)} €</div>
      <div class="bar-stack" style="height:${barHeight}%">
        ${item.other > 0 ? `<div class="income-bar other" style="flex:${otherShare}"></div>` : ""}
        <div class="income-bar pub" style="flex:${100 - otherShare}"></div>
      </div>
      <div class="income-bar-label">${item.year}</div>
    </div>`;
    })
    .join("");

  const wrap = el.closest(".income-chart-wrap");
  if (!wrap) {
    return;
  }

  wrap.querySelectorAll(".median-line, .median-label").forEach((line) => {
    line.remove();
  });

  const context = detail.context || {};
  if (maxIncome <= 0) {
    return;
  }

  const chartHeight = 140;
  const referenceLines = [
    {
      value: Number(context.slovak_median_income || 0),
      label:
        Number(context.slovak_median_income || 0) > 0
          ? `SK medián: ${fmt(Number(context.slovak_median_income || 0))} €`
          : "",
      color: "#94a3b8",
    },
    {
      value: Number(context.median_income || 0),
      label:
        Number(context.median_income || 0) > 0
          ? `medián funkc.: ${fmt(Number(context.median_income || 0))} €`
          : "",
      color: "#3b82f6",
    },
  ];

  referenceLines.forEach((line) => {
    if (!line.value || !line.label) {
      return;
    }
    const percent = (line.value / maxIncome) * 100;
    if (percent > 95 || percent < 1) {
      return;
    }

    const position = chartHeight - (percent / 100) * (chartHeight - 10) + 10;
    const lineEl = document.createElement("div");
    lineEl.className = "median-line";
    lineEl.style.top = `${position}px`;
    lineEl.style.borderColor = line.color;

    const labelEl = document.createElement("div");
    labelEl.className = "median-label";
    labelEl.style.top = `${position}px`;
    labelEl.textContent = line.label;

    wrap.appendChild(lineEl);
    wrap.appendChild(labelEl);
  });
}

function renderTimeline(detail) {
  const el = document.getElementById("timeline");
  if (!el) {
    return;
  }

  el.innerHTML = detail.timeline
    .map((item, index) => {
      const diff = item.diff;
      const previous = index > 0 ? detail.timeline[index - 1] : null;
      let badge = "";
      let details = "";
      let compareLink = "";

      if (diff.type === "new") {
        badge = '<span class="timeline-badge badge-new">nový záznam</span>';
      } else if (diff.type === "unchanged") {
        badge = '<span class="timeline-badge badge-unchanged">bez zmien</span>';
      } else if (diff.type === "changed") {
        badge = `<span class="timeline-badge badge-changed">${diff.changes.length} ${
          diff.changes.length === 1 ? "zmena" : "zmien"
        }</span>`;
        details =
          '<div class="change-detail">' +
          diff.changes
            .map((change) => {
              const label = fieldLabel(change.field);
              if (change.field === "income" && change.old_total != null) {
                const sign = change.delta > 0 ? "+" : "";
                return `<div><span class="field-name">${label}:</span> <span class="old-val">${fmt(change.old_total)} €</span> → <span class="new-val">${fmt(change.new_total)} €</span> (${sign}${fmt(change.delta)} €)</div>`;
              }
              if (change.old_count != null) {
                const delta = change.new_count - change.old_count;
                if (delta === 0) {
                  return "";
                }
                const sign = delta > 0 ? "+" : "";
                return `<div><span class="field-name">${label}:</span> ${change.old_count} → ${change.new_count} (${sign}${delta})</div>`;
              }
              return `<div><span class="field-name">${label}</span></div>`;
            })
            .filter(Boolean)
            .join("") +
          "</div>";
      }

      if (previous) {
        compareLink = `<button type="button" class="timeline-compare-link" data-compare-years="${previous.year},${item.year}">Porovnať roky</button>`;
      }

      return `<div class="timeline-entry"><span class="timeline-year">${item.year}</span>${badge}${details}${compareLink}</div>`;
    })
    .join("");
}

function esc(value) {
  const el = document.createElement("span");
  el.textContent = value || "";
  return el.innerHTML;
}

function fmt(value) {
  return value != null ? value.toLocaleString("sk-SK") : "";
}

function row(label, value) {
  return `<div class="data-row"><div class="data-label">${label}</div><div class="data-value">${value}</div></div>`;
}

function section(title, renderFn) {
  return `<div class="section"><h3>${title}</h3><div class="section-card">${renderFn()}</div></div>`;
}

function empty(text) {
  return `<span class="section-empty">${text || "neuvedené"}</span>`;
}

function itemsList(items, renderFn) {
  return `<ul class="items-list">${items.map((item) => `<li>${renderFn(item)}</li>`).join("")}</ul>`;
}

function progressBar(percent, variant = "") {
  return `<div class="ctx-meter" aria-hidden="true"><div class="ctx-meter__fill${variant ? ` ${variant}` : ""}" style="width:${Math.max(Number(percent || 0), 4)}%"></div></div>`;
}

function fieldLabel(key) {
  return (
    {
      income: "príjmy",
      employment: "zamestnanie",
      business_activity: "podnikanie",
      positions: "funkcie",
      real_estate: "nehnuteľnosti",
      movable_property: "hnuteľný majetok",
      obligations: "záväzky",
      vehicles: "vozidlá",
      gifts: "dary",
      property_rights: "majetkové práva",
      public_function: "verejná funkcia",
      incompatibility: "nezlučiteľnosť",
      use_of_others_real_estate: "užívanie nehnuteľností",
    }[key] || key
  );
}

initDetail();
