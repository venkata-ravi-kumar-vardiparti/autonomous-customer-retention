"""Read-only repositories over the Governed Data Layer.

Every public function returns churnguard.data.provenance.RepoResult[T],
where T is an existing, untouched contract model (or a plain list/dict of
strings where no contract model exists for the shape, e.g. active
promotion names or note text). No repository ever returns a raw DB row.
"""
