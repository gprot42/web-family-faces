import { matchPeople, splitNameMatch, uniqueCatalogPerson } from "../nameSuggest.js";
import { tip } from "../tip.js";

export default function NameSuggest({ query, people, excludeId, activeIndex, onPick }) {
  const items = matchPeople(query, people, { excludeId });
  if (!items.length) return null;
  const unique = uniqueCatalogPerson(query, people, { excludeId });
  return (
    <div className="name-suggest" onClick={(e) => e.stopPropagation()}>
      <div className="cluster-label">In the catalog</div>
      <ul className="name-suggest-list" role="listbox" aria-label="Matching names">
        {items.map((person, i) => {
          const bits = splitNameMatch(query, person.name);
          const keyN = i + 1;
          return (
            <li key={person.id}>
              <button
                type="button"
                role="option"
                aria-selected={i === activeIndex}
                className={`name-suggest-item ${i === activeIndex ? "active" : ""}`}
                onMouseDown={(e) => {
                  e.preventDefault();
                  onPick(person);
                }}
                {...tip(`Use ${person.name} from the catalog. Shortcut ${keyN}.`)}
              >
                <span className="name-suggest-n" aria-hidden="true">{keyN}</span>
                {person.cover_url ? <img src={person.cover_url} alt="" /> : <span className="person-picker-gap" />}
                <span className="name-suggest-text">
                  {bits.head ? (
                    <>
                      {bits.head}
                      <span className="name-suggest-rest">{bits.rest}</span>
                    </>
                  ) : (
                    person.name
                  )}
                  {person.nickname ? <span className="name-suggest-nick"> · {person.nickname}</span> : null}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {unique ? <p className="hint">Enter uses {unique.name}.</p> : null}
    </div>
  );
}
