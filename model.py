import collections
import collections.abc
import functools
import json
import os
import pickle as pickle
import random

from typing import Any, Callable

import yaml

from . import view
from .puid import inferPUID
from .rc import OTIS_EVIL_JSON_PATH, SEPARATOR, SORT_TAGS, VON_BASE_PATH, VON_CACHE_PATH, VON_INDEX_PATH  # NOQA


def shortenPath(path: str):
	return os.path.relpath(path, VON_BASE_PATH)


def completePath(path: str):
	return os.path.join(VON_BASE_PATH, path)


def vonOpen(path: str, *args: Any, **kwargs: Any):
	return open(completePath(path), *args, **kwargs)


class pickleObj(collections.abc.MutableMapping):
	def _initial(self) -> Any:
		return {}

	def __init__(self, path: str, mode='rb'):
		if not os.path.isfile(path) or os.path.getsize(path) == 0:
			self.store = self._initial()
		else:
			with vonOpen(path, 'rb') as f:
				self.store = pickle.load(f)  # type: ignore
		self.path = path
		self.mode = mode

	def __enter__(self):
		return self

	def __exit__(self, *_: Any):
		if self.mode == 'wb':
			with vonOpen(self.path, 'wb') as f:
				pickle.dump(self.store, f)  # type: ignore

	def __getitem__(self, key: Any):
		try:
			return self.store[key]
		except IndexError:
			raise IndexError(f"{key} not a valid key")

	def __setitem__(self, key: int | str, value: Any):
		if isinstance(self.store, list):
			assert isinstance(key, int)
			self.store[key] = value
		else:
			assert isinstance(key, str)
			self.store[key] = value

	def __delitem__(self, key: str):
		if isinstance(self.store, list):
			assert isinstance(key, int)
			del self.store[key]
		else:
			assert isinstance(key, str)
			del self.store[key]

	def __iter__(self):
		return iter(self.store)

	def __len__(self):
		return len(self.store)

	def set(self, store: Any):
		self.store = store


class pickleDictVonIndex(pickleObj):
	store: dict[str, 'PickleMappingEntry']
	__getitem__: Callable[..., 'PickleMappingEntry']

	def _initial(self) -> dict[str, 'PickleMappingEntry']:
		return {}


class pickleListVonCache(pickleObj):
	store: list['PickleMappingEntry']

	def __getitem__(self, idx: int) -> 'PickleMappingEntry':
		return super().__getitem__(idx)

	def _initial(self) -> list['PickleMappingEntry']:
		return []

	def set(self, store: list['PickleMappingEntry']):
		for i in range(len(store)):
			store[i].i = i
		self.store = store


def VonIndex(mode='rb'):
	return pickleDictVonIndex(VON_INDEX_PATH, mode)


def VonCache(mode='rb'):
	return pickleListVonCache(VON_CACHE_PATH, mode)


@functools.total_ordering
class GenericItem:  # superclass to Problem, PickleMappingEntry
	desc = ""  # e.g. "Fiendish inequality"
	source = ""  # used as problem ID, e.g. "USAMO 2000/6"
	tags: list[str] = []  # tags for the problem
	path = ""  # path to problem TeX file
	i: int | None = None  # position in Cache, if any
	author: str | None = None  # default
	hardness: int | None = None  # default
	url: str | None = None

	@property
	def n(self):
		return self.i + 1 if self.i is not None else None

	@property
	def sortvalue(self):
		for i, d in enumerate(SORT_TAGS):
			if d in self.tags:
				return i
		return -1

	@property
	def sortstring(self):
		for d in SORT_TAGS:
			if d in self.tags:
				return d
		return "NONE"

	@property
	def sortkey(self):
		if type(self.hardness) == int:
			return (self.sortvalue, self.hardness, self.source)
		else:
			return (self.sortvalue, -1, self.source)

	def __eq__(self, other) -> bool:
		return self.sortkey == other.sortkey

	def __lt__(self, other) -> bool:
		return self.sortkey < other.sortkey


class Problem(GenericItem):
	bodies: list[str] = []  # statement, sol, comments, ...

	def __init__(self, path: str, **kwargs):
		self.path = path
		for key in kwargs:
			setattr(self, key, kwargs[key])

	@property
	def state(self) -> str:
		return self.bodies[0]

	def __repr__(self):
		return self.source

	@property
	def entry(self) -> 'PickleMappingEntry':
		"""Returns an PickleMappingEntry for storage in pickle"""
		return PickleMappingEntry(
			source=self.source,
			desc=self.desc,
			author=self.author,
			url=self.url,
			hardness=self.hardness,
			tags=self.tags,
			path=self.path,
			i=self.i
		)

	@property
	def full(self) -> 'Problem':
		view.warn("sketchy af")
		return self


class PickleMappingEntry(GenericItem):
	def __init__(self, **kwargs):
		for key in kwargs:
			if kwargs[key] is not None:
				setattr(self, key, kwargs[key])

	# search things
	def hasTag(self, tag):
		return tag.lower() in [_.lower() for _ in self.tags]

	def hasTerm(self, term):
		blob = self.source + ' ' + self.desc
		if self.author is not None:
			blob += ' ' + self.author
		return (
			term.lower() in blob.lower() or term in self.tags or
			term.upper() in inferPUID(self.source)
		)

	def hasAuthor(self, name):
		if self.author is None:
			return False
		haystacks = self.author.lower().strip().split(' ')
		return name.lower() in haystacks

	def hasSource(self, source):
		return source.lower() in self.source.lower()

	def __repr__(self):
		return self.source

	@property
	def secret(self):
		return 'SECRET' in self.source or 'secret' in self.tags

	@property
	def entry(self):
		view.warn("sketchy af")
		return self

	@property
	def full(self) -> Problem:
		p = makeProblemFromPath(self.path)
		assert p is not None
		return p


def getcwd():
	true_dir = os.getcwd()
	if true_dir.startswith(VON_BASE_PATH) and true_dir != VON_BASE_PATH:
		return os.path.relpath(true_dir, VON_BASE_PATH)
	else:
		return ''


def getCompleteCwd():
	return completePath(getcwd())


def makeProblemFromPath(path: str) -> Problem:
	# Creates a problem instance from a source, without looking at Index
	with vonOpen(path, 'r') as f:
		text = ''.join(f.readlines())
	x = text.split(SEPARATOR)
	data = yaml.safe_load(x[0])
	assert data is not None
	data['bodies'] = [_.strip() for _ in x[1:]]
	return Problem(path, **data)


def getAllProblems() -> list[Problem]:
	ret: list[Problem] = []
	for root, _, filenames in os.walk(VON_BASE_PATH):
		for fname in filenames:
			if not fname.endswith('.tex'):
				continue
			path = shortenPath(os.path.join(root, fname))
			p = makeProblemFromPath(path)
			if p is not None:
				ret.append(p)
	return ret


def getEntryByCacheNum(n: int) -> PickleMappingEntry:
	with VonCache() as cache:
		return cache[n - 1]


def getEntryBySource(source: str) -> PickleMappingEntry | None:
	with VonIndex() as index:
		return index[source] if source in index else None


def getEntryByKey(key: str):
	# TODO this shouldn't actually be in model, but blah
	if key.isdigit():
		return getEntryByCacheNum(n=int(key))
	else:
		return getEntryBySource(source=key)


def addProblemByFileContents(path: str, text: str):
	with vonOpen(path, 'w') as f:
		print(text, file=f)
	view.log("Wrote to " + path)
	# Now update cache
	p = makeProblemFromPath(shortenPath(path))
	addProblemToIndex(p)
	return p


def viewDirectory(path: str):
	problems: list['Problem'] = []
	dirs: list[str] = []
	for item_path in os.listdir(path):
		abs_item_path = os.path.join(path, item_path)
		if os.path.isfile(abs_item_path) and abs_item_path.endswith('.tex'):
			problem = makeProblemFromPath(abs_item_path)
			assert problem is not None
			problems.append(problem)
		elif os.path.isdir(abs_item_path):
			dirs.append(item_path)
		else:
			pass  # not TeX or directory
	dirs.sort()
	entries = [p.entry for p in problems]
	entries.sort()
	if len(entries) > 0:
		setCache(entries)
	return (entries, dirs)


def runSearch(
	terms: list[str] = [],
	tags: list[str] = [],
	sources: list[str] = [],
	authors: list[str] = [],
	path='',
	refine=False,
	alph_sort=False,
	in_otis=None
) -> list[PickleMappingEntry]:
	if in_otis is not None and OTIS_EVIL_JSON_PATH is not None:
		with open(OTIS_EVIL_JSON_PATH) as f:
			evil_json = json.load(f)
			otis_used_sources = evil_json.values()
	else:
		otis_used_sources = None

	def _matches(entry: PickleMappingEntry):
		if otis_used_sources is not None:
			_used: bool = (entry.source in otis_used_sources) or entry.hasTag('waltz')
			if _used and in_otis is False:
				return False
			elif not _used and in_otis is True:
				return False

		return (
			all([entry.hasTag(_) for _ in tags]) and all([entry.hasTerm(_) for _ in terms]) and
			all([entry.hasSource(_) for _ in sources]) and
			all([entry.hasAuthor(_) for _ in authors]) and entry.path.startswith(path)
		)

	if refine is False:
		with VonIndex() as index:
			result: list[PickleMappingEntry] = [entry for entry in index.values() if _matches(entry)]
	else:
		with VonCache() as cache:
			result = [entry for entry in cache.values() if _matches(entry)]
	if alph_sort:
		result.sort(key=lambda e: e.source)
	else:
		result.sort()
	if len(result) > 0:
		setCache(result)
	return result


def augmentCache(*entries: PickleMappingEntry):
	with VonCache('wb') as cache:
		cache.set(cache.store + list(entries))


def setCache(entries):
	with VonCache('wb') as cache:
		cache.set(entries)


def clearCache():
	with VonCache('wb') as cache:
		cache.set([])


def readCache():
	with VonCache() as cache:
		return cache


# A certain magical Index~ <3


def addEntryToIndex(entry: PickleMappingEntry):
	with VonIndex('wb') as index:
		index[entry.source] = entry


def updateEntryByProblem(old_entry: PickleMappingEntry, new_problem: Problem):
	new_problem.i = old_entry.i
	new_entry = new_problem.entry

	with VonIndex('wb') as index:
		if old_entry.source != new_entry.source:
			del index[old_entry.source]
		index[new_entry.source] = new_entry
	with VonCache('wb') as cache:
		for i, entry in enumerate(cache):
			if entry.source == old_entry.source:
				new_entry.i = i
				cache[i] = new_entry
				break
		else:
			cache.set(cache.store + [new_entry])
	return index[new_entry.source]


def addProblemToIndex(problem):
	with VonIndex('wb') as index:
		p = problem
		index[p.source] = p.entry
		return index[p.source]


def setEntireIndex(d):
	with VonIndex('wb') as index:
		index.set(d)


def rebuildIndex():
	d: dict[str, PickleMappingEntry] = {}
	for p in getAllProblems():
		if p.source in d:
			fake_source = f"DUPLICATE {random.randrange(10**6, 10**7)}"
			view.error(p.source + " is being repeated, replacing with " + fake_source)
			p.source = fake_source
		d[p.source] = p.entry
	setEntireIndex(d)
