import { Link } from "react-router-dom";

export default function Help() {
  return (
    <div className="help">
      <div className="page-head">
        <div>
          <p className="eyebrow">Help</p>
          <h1>How Family Faces works</h1>
          <p className="lede">
            A local catalog for a family album. Faces are grouped on this machine. You name a
            person once. Originals on the NAS are never rewritten.
          </p>
        </div>
      </div>

      <div className="help-grid">
        <section className="card help-block">
          <h2>1. Choose folders, then Find Known Faces</h2>
          <p>
            On the home page click <strong>Choose folders</strong>. The app connects the Synology
            NAS (Finder may ask for the login once). Open or tick the albums you want.{" "}
            <strong>Find Known Faces</strong> starts on its own for each new album. On Folder View,{" "}
            <strong>Find Known Faces in new folders</strong> lists those albums first so you can
            confirm.
          </p>
          <p>
            The app reads photos, finds known faces, and groups people who look the same. If the
            first model is unsure, a second local model (AdaFace) tries the same face on this
            machine. A name is applied only when that second look is sure and does not disagree
            with the first. Those names still appear under Check names. It never
            moves or rewrites the files. A later run only looks at new photos. In{" "}
            <Link to="/settings">Settings</Link> you can turn on <strong>Auto-update listed
            folders</strong> so new files on those albums or the NAS are picked up in the
            background. Uncheck <strong>Find faces on new photos</strong> if you want them listed
            without AI tagging or face scanning.{" "}
            <strong>Check first names against male / female face guesses</strong> can be turned off
            if a man is skipped because the detector guessed female.
          </p>
        </section>

        <section className="card help-block">
          <h2>2. Clusters to name</h2>
          <p>
            Click <Link to="/to-name">Clusters to name</Link>. Type a
            name on the largest cluster. That name is applied to the whole cluster, and to other
            faces that clearly match.
          </p>
          <ul>
            <li>
              If two people were mixed, click the extra faces and choose{" "}
              <strong>Name these separately</strong>.
            </li>
            <li>
              <strong>Unknown name of person</strong> keeps them as a person when you do not know
              the name yet. They are stored in the database and can be renamed later on Faces in DB View.
              Type in <strong>Save name</strong> to search people already in the catalog; pick a match
              to join this identity with them, or save a new name. On that page you can also mark
              someone Family, Work, or Other.
            </li>
            <li>
              <strong>AI</strong> on a Clusters to name cluster (or <strong>Look up famous face</strong> on a
              photo) sends that face crop (never the original photo) plus text clues: filename,
              folder, dates, EXIF, and people already named nearby. Grok can search public photos.
              You still confirm the name. You can reply “try again” or “not this person” and see how
              sure it is.
            </li>
            <li>
              <strong>Identify all</strong> on Clusters to name asks Yes or No, then matches clusters to people already in the
              catalog, then looks remaining clusters up. Only very sure names are applied (about 80%+
              for someone already in the catalog, 90%+ for a new public figure). Catalog
              matches still appear under Check names. Use <strong>AI</strong> on a cluster to look
              that one up without waiting for the batch.
            </li>
            <li><strong>Not a person</strong> is for statues, paintings, or objects. They stay hidden, and similar ones are ignored later.</li>
            <li>
              On Clusters to name, <strong>×</strong> on a named face in the right-hand list hides them from
              tagging. They stay in Faces in DB View. <strong>Hidden from tagging</strong> shows
              them again.
            </li>
            <li>
              <Link to="/review">Check names</Link> lists faces the matcher attached on its own.
              Keep the right ones. Not this person sends a face back to Clusters to name.
            </li>
            <li>
              <strong>Search</strong> in the bottom right looks up a person by name, or matches a
              snapshot to a picture already in the catalog, and to named people. On a person page you can add a{" "}
              <strong>nickname</strong>; search finds them by that as well as the full name.
            </li>
          </ul>
        </section>

        <section className="card help-block">
          <h2>3. Faces in DB View — identified faces in the database</h2>
          <p>
            <Link to="/people">Faces in DB View</Link> is the catalog of faces you have identified.
            Those identities are stored in the local database, not written onto the photos.
            Folder View is the albums. Click a face to open that person. Right-click a face and choose{" "}
            <strong>Find a better photo</strong> to try the next cover; right-click again for another.
            Use <strong>Search</strong> in
            the bottom right to find someone by name, or upload a snapshot to match a picture already
            in the catalog. On a person page, type in <strong>Save name</strong> to search people already
            in the catalog. Pick a match to join this identity with them; Enter uses the unique match.
            A name that is not in the catalog is saved on this card.{" "}
            <strong>Download photos</strong> saves a zip of every
            picture they are named in; <strong>With labels</strong> saves copies with name tags drawn
            on. Album files stay where they are.{" "}
            <strong>Show in family tree</strong> opens that person on the Family tree page.
            A child and an
            80-year-old will usually be two cards — only join them if you are sure they are the same
            person.
          </p>
        </section>

        <section className="card help-block">
          <h2>4. Family tree — load a .ged file</h2>
          <p>
            <Link to="/tree">Family tree</Link> opens a GEDCOM file exported from Ancestry,
            FamilySearch, Gramps, or MacFamilyTree. Choose a <code>.ged</code> file to see people,
            ancestors as far as the file goes, spouses, and children drawn as a tree. Search by name to jump to someone.
            Scroll or pinch to zoom, drag to move, and click a person to center the tree on them.
            <strong> Entire tree</strong> shows everyone in the file, not only this person’s close relatives.
            Full screen expands the tree; Esc or Exit leaves it.
            The file is stored on this Mac. If a name already exists
            in Faces in DB View, you can jump to that person. Photo originals are not touched.
          </p>
        </section>

        <section className="card help-block">
          <h2>Leftovers</h2>
          <p>
            Anything still unnamed can be finished in <Link to="/photos">Folder View</Link>. Open a
            picture, click a face, pick a name. Right-click an album to rename it in Folder View;
            the folder on disk is not renamed. In <Link to="/settings">Settings</Link> you can put
            the name on the person, above the head, or below the body.{" "}
            <Link to="/photos?by=person">View by person</Link> keeps each named person in their own list
            so two people are not mixed together. Right-click a photo and add a <strong>tag</strong>
            (holiday, school, a year), then use <Link to="/photos?by=tag">View by tag</Link> to see
            every picture with that tag. Tags stay in the catalog, not on the file.
          </p>
        </section>
      </div>

      <section className="card help-block">
        <h2>Keyboard on a photo</h2>
        <div className="keys">
          <div><kbd>1</kbd>–<kbd>5</kbd> assign a suggestion</div>
          <div>Type a first name to search the catalog · <kbd>tab</kbd> fills the rest · <kbd>enter</kbd> uses the match</div>
          <div>Comment on a photo from the naming view or the right-click menu. It is not written onto the file.</div>
          <div>Drag a name on the picture to move it. Double-click the name to put it back.</div>
          <div>
            <strong>Add a face</strong> is for someone the detector missed. Draw a box around
            the head. The app picks up that face, then you can name them as usual.
          </div>
          <div>
            <strong>Re-identify faces</strong> matches unnamed people against the catalog: closest
            named photos, several examples of each person, and people already named in that album.
            If InsightFace is not sure, AdaFace (a second local model) retries the same face.
            On a class photo it will not copy one name onto every face.
            Faces whose names you removed are tried again. If everyone on the photo is already
            named, it returns immediately. Use <strong>Add a face</strong> if someone was missed.
            <strong>Undo names</strong> takes those auto-matches back if too many are wrong. Names
            you typed stay. On a group photo, faces hidden as “not a person” are put back in the
            unnamed pool so they can be named.
          </div>
          <div>
            <strong>Sharpen</strong> asks Grok Imagine for a crisper 2K preview of this picture,
            with extra clarity on faces.
            Zoom still goes to <strong>400%</strong> on that preview. Toggle{" "}
            <strong>Show original</strong> any time. The NAS original is never overwritten;
            the preview lives only in the app’s data folder. Needs an xAI key or SuperGrok in
            Settings. A copy of the photo is sent to Grok, not the original file on disk.
          </div>
          <div>
            <strong>Change with Grok</strong> sends the same copy to Grok Imagine with your
            prompt (restore colour, black and white, repair scratches, and so on). Toggle the
            preview, or click the badge to describe another change. The original file is never
            overwritten.
          </div>
          <div><kbd>⌘Z</kbd> / <kbd>Ctrl+Z</kbd> undo the last name, hide, restore, comment, or re-identify on this photo. Press again to undo the change before that.</div>
          <div><kbd>n</kbd> new person</div>
          <div><kbd>u</kbd> unassign</div>
          <div><kbd>j</kbd> / <kbd>k</kbd> next / previous face</div>
          <div><kbd>l</kbd> hide or show name labels on the picture</div>
          <div>
            <strong>Smart</strong> (then Rows, Halo, Numbers) tries another way to place names on a
            crowded photo. Smart parks numbers and names above the head, not on the eyes. Rows puts
            the back row above and the front row below. Halo parks names around the group. Numbers
            puts a number on each face; the full name stays in the list. Names you dragged stay put.
          </div>
          <div><kbd>←</kbd> / <kbd>→</kbd> previous / next photo. Name fields are not auto-selected, so the arrows keep changing pictures.</div>
          <div>
            <strong>Remove unnamed</strong> hides every unnamed face on this photo as not a person.
            Named people stay. Undo puts the boxes back. Other photos are not changed.
          </div>
          <div>Right-click a photo to copy it, rotate it, add a tag, add a face, re-identify unnamed faces, sharpen a temporary Grok preview, change it with a Grok prompt, remove a named person or an unnamed face, remove all unnamed faces, or delete it from the catalog. <strong>Copy photo</strong> includes the name tags when they are shown on the picture. <strong>Download photo</strong> saves the file; <strong>Download with labels</strong> saves a copy with name tags. The original file stays where it is.</div>
          <div>
            <strong>Add a note</strong> on a photo is about the picture. <strong>Add a tag</strong> is
            a short label (Christmas, school, 2018): right-click the photo, type in Add a tag, press
            Enter. Existing tags in that menu open View by tag, or remove the tag.{" "}
            <strong>Add a note</strong> on a face is about that person in that picture. Notes on Faces
            in DB View are about the person. All of them stay in the catalog, never on the original
            file.
          </div>
          <div>Click the photo or <strong>Fullscreen</strong> after naming · drag the dotted handle on the options bar to move it · Hide labels / Show labels on the picture (<kbd>l</kbd>) · ← → other photos · zoom controls sit on the right of the picture · <kbd>+</kbd> / <kbd>−</kbd> or pinch to zoom · <kbd>0</kbd> or click to fit · any other key or click to exit</div>
          <div>
            <strong>Play album</strong> or <strong>Play person</strong> walks the pictures in date
            order, 2.5 seconds each (change this in Settings). <kbd>space</kbd> pause · <kbd>esc</kbd> exit.
            <strong> Download photos</strong> on a person page saves those pictures as a zip;
            <strong> With labels</strong> draws names on copies.
          </div>
        </div>
      </section>

      <section className="card help-block safety">
        <h2>Your originals stay untouched</h2>
        <ul>
          <li>Photos are never moved, renamed, copied, or sorted into new folders.</li>
          <li>Photos are opened read-only. The app cannot write those files, including EXIF.</li>
          <li>
            <strong>Sharpen</strong> stores a Grok Imagine preview under <code>data/sharpen</code>.
            <strong> Change with Grok</strong> stores a prompt edit under <code>data/imagine</code>.
            Neither replaces the original photo.
          </li>
          <li>
            Names live in the local catalog (<code>data/photosort.db</code>). Each album folder
            also gets a portable <code>.photosort.json</code> so a copied folder keeps its names.
            Originals and EXIF are never written.
          </li>
          <li>
            <strong>Purge faces from database</strong> on Settings clears names from the app
            catalog. Photos, statue marks, and each album’s <code>.photosort.json</code> stay.
          </li>
          <li>Thumbnails and face crops live under the app <code>data/</code> folder, not in the album.</li>
          <li>
            <strong>Check photos unchanged</strong> asks first, then re-hashes every indexed file and reports
            unchanged / changed / missing.
          </li>
          <li>
            <strong>Back up names</strong> writes a gzip of the name catalog. After you choose it, the
            button stays marked <strong>Names backed up</strong>. The app also backs up automatically
            while it is open, after Find Known Faces, and keeps the last 14 copies. Photo files stay
            on the NAS — back those up separately.
          </li>
        </ul>
      </section>

      <p className="hint">
        Why originals stay untouched: <Link to="/about">About</Link>.
      </p>
    </div>
  );
}
