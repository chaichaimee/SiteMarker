# markerEngine.py

import os
import json
import re
import functools
import unicodedata
import addonHandler
import config
from logHandler import log
import api
import textInfos

addonHandler.initTranslation()

class MarkerEngine:
	def __init__(self):
		userConfigPath = config.getUserDefaultConfigPath()
		self.sitesDir = os.path.join(userConfigPath, "ChaiChaimee", "SiteMarker", "site")
		self._ensureDirectoryStructure()
		self._siteFileNames = self._scanSiteFiles()
		self.siteCache = {}

	def _ensureDirectoryStructure(self):
		try:
			os.makedirs(self.sitesDir, exist_ok=True)
		except Exception as e:
			log.error(f"Failed to create sites directory: {e}")

	def _scanSiteFiles(self):
		if not os.path.exists(self.sitesDir):
			return []
		return [f for f in os.listdir(self.sitesDir) if f.endswith(".json")]

	def _getSiteFilePath(self, siteName):
		safeName = re.sub(r'[\\/*?:"<>|]', "_", siteName)
		return os.path.join(self.sitesDir, f"{safeName}.json")

	def _normalize(self, text):
		if not text:
			return text
		return unicodedata.normalize("NFC", text)

	def getAllSiteNames(self):
		return [name[:-5] for name in self._siteFileNames if name.endswith(".json")]

	def getSiteConfig(self, siteName):
		if siteName in self.siteCache:
			return self.siteCache[siteName]
		filePath = self._getSiteFilePath(siteName)
		if not os.path.exists(filePath):
			return None
		try:
			with open(filePath, "r", encoding="utf-8") as f:
				configData = json.load(f)
				self.siteCache[siteName] = configData
				return configData
		except Exception as e:
			log.error(f"Failed to load site {siteName}: {e}")
			return None

	def isSiteExistsForUrl(self, url):
		if not url:
			return False
		for siteName in self.getAllSiteNames():
			siteConfig = self.getSiteConfig(siteName)
			if siteConfig and self.checkUrlMatch(siteConfig.get("matchType", 0), siteConfig.get("pattern", ""), url):
				return True
		return False

	def saveSiteConfiguration(self, siteName, siteConfig):
		self.siteCache[siteName] = siteConfig
		filePath = self._getSiteFilePath(siteName)
		try:
			with open(filePath, "w", encoding="utf-8") as f:
				json.dump(siteConfig, f, ensure_ascii=False, indent=4)
			if siteName not in [name[:-5] for name in self._siteFileNames]:
				self._siteFileNames.append(f"{siteName}.json")
		except Exception as e:
			log.error(f"Failed to save site {siteName}: {e}")

	def deleteSiteConfiguration(self, siteName):
		if siteName in self.siteCache:
			del self.siteCache[siteName]
		filePath = self._getSiteFilePath(siteName)
		try:
			if os.path.exists(filePath):
				os.remove(filePath)
				self._siteFileNames = [f for f in self._siteFileNames if f != f"{siteName}.json"]
			return True
		except Exception as e:
			log.error(f"Failed to delete site {siteName}: {e}")
			return False

	def checkUrlMatch(self, matchType, pattern, url):
		if not url or not pattern:
			return False
		url = url.strip().lower()
		pattern = pattern.strip().lower()
		normalizedUrl = self._normalize(url)
		normalizedPattern = self._normalize(pattern)
		if matchType == 0:  # Domain Only
			domain = url.split("//")[-1].split("/")[0]
			return domain == pattern
		elif matchType == 1:  # Include Subdomains
			domain = url.split("//")[-1].split("/")[0]
			return domain == pattern or domain.endswith("." + pattern)
		elif matchType == 2:  # Contain Substring
			return normalizedPattern in normalizedUrl
		elif matchType == 3:  # Exact Matching
			return normalizedUrl == normalizedPattern
		elif matchType == 4:  # Regular Expression
			try:
				return re.search(pattern, normalizedUrl, re.IGNORECASE | re.UNICODE) is not None
			except Exception:
				return False
		return False

	def getMarkersForUrl(self, url):
		if not url:
			return {}
		for siteName in self.getAllSiteNames():
			siteConfig = self.getSiteConfig(siteName)
			if not siteConfig:
				continue
			if self.checkUrlMatch(siteConfig.get("matchType", 0), siteConfig.get("pattern", ""), url):
				markersList = siteConfig.get("markers", [])
				mappedMarkers = {}
				for item in markersList:
					key = item.get("keystroke", "j").strip().lower()
					if key not in mappedMarkers:
						mappedMarkers[key] = []
					mappedMarkers[key].append(item)
				# No explicit sorting here – ordering is later handled by _sortMarkersByDocumentOrder
				return mappedMarkers
		return {}

	@functools.lru_cache(maxsize=64)
	def _build_composite_regex(self, markers_tuple):
		regex_parts = []
		for idx, (pattern, mode) in enumerate(markers_tuple):
			if mode == 0:
				part = re.escape(pattern)
			elif mode == 1:
				part = f"^{re.escape(pattern)}$"
			else:
				part = pattern
			regex_parts.append(f"(?P<q{idx}>{part})")
		return re.compile("|".join(regex_parts), re.IGNORECASE | re.UNICODE)

	@functools.lru_cache(maxsize=256)
	def _cached_match(self, normalized_text, markers_tuple):
		composite = self._build_composite_regex(markers_tuple)
		m = composite.search(normalized_text)
		if m:
			for key, val in m.groupdict().items():
				if val is not None and key.startswith("q"):
					return int(key[1:])
		return -1

	def matchParagraph(self, textInfo, markerDataList):
		raw_text = textInfo.text.strip()
		if not raw_text:
			return None, None

		normal_text = self._normalize(raw_text)
		normal_markers = []
		regex_markers = []

		for idx, marker in enumerate(markerDataList):
			if marker.get("matchMode", 0) == 2:
				regex_markers.append((idx, marker))
			else:
				pattern = marker.get("pattern", "")
				if pattern:
					normalized_pattern = self._normalize(pattern)
					normal_markers.append((normalized_pattern, marker.get("matchMode", 0), idx))

		if normal_markers:
			markers_tuple = tuple((p, m) for p, m, _ in normal_markers)
			match_idx = self._cached_match(normal_text, markers_tuple)
			if match_idx >= 0:
				original_idx = normal_markers[match_idx][2]
				return markerDataList[original_idx], None

		for idx, marker in regex_markers:
			pattern = marker.get("pattern", "")
			if not pattern:
				continue
			try:
				if re.search(pattern, raw_text, re.IGNORECASE | re.UNICODE):
					return marker, None
			except Exception:
				continue

		return None, None

	def cleanUp(self):
		self.siteCache.clear()
		self._build_composite_regex.cache_clear()
		self._cached_match.cache_clear()