--[[
  Lunelis <-> darktable: ratings, colour labels and rejects, both ways.

  Lunelis keeps its sidecars out of your photo folders, so darktable can't see
  its ratings on its own. This script swaps them through two small files in a
  shared "exchange" folder (Lunelis writes the path in when it installs this):

    lunelis-ratings.tsv     written by Lunelis   path, stars (-1 = reject), label, changed
    darktable-ratings.tsv   written here         path, rating, labels, changed

  * From Lunelis: at start-up, when you switch views, and on "Sync with
    Lunelis" (lighttable, right panel) or its shortcut. Only rows changed since
    the last sync are applied, and only if darktable's own value differs.
  * To Lunelis: what you changed in darktable since the last sync (compared
    with a snapshot, darktable-snapshot.tsv), on the same occasions and when
    darktable closes. Lunelis applies it unless it changed that photo later.
  * darktable can give a photo several colour labels; Lunelis has one. A photo
    with one label here gets Lunelis's change; one with several only ever gains
    Lunelis's label - none of yours is removed.

  Nothing here edits a photo, a darktable history or an XMP sidecar directly;
  darktable writes its own sidecars as usual.
]]

local dt = require "darktable"

local MODULE = "lunelis"
local DEFAULT_EXCHANGE = [[@EXCHANGE@]]          -- filled in by Lunelis's installer
local LABELS = {"red", "yellow", "green", "blue", "purple"}

dt.preferences.register(MODULE, "exchange", "directory", "Lunelis: exchange folder",
  "The folder Lunelis and darktable swap ratings through (Lunelis > Settings > darktable)",
  DEFAULT_EXCHANGE)

local M = {}

local function exchange()
  local dir = dt.preferences.read(MODULE, "exchange", "directory")
  if dir == nil or dir == "" then dir = DEFAULT_EXCHANGE end
  return dir
end

local function join(dir, name)
  local sep = dir:find("/", 1, true) and not dir:find("\\", 1, true) and "/" or "\\"
  if dir:sub(-1) == "\\" or dir:sub(-1) == "/" then return dir .. name end
  return dir .. sep .. name
end

-- One way of spelling a path for matching: backslashes, lower case (Windows).
function M.key(path)
  return (path:gsub("/", "\\")):lower()
end

local function utc_now()
  return os.date("!%Y-%m-%dT%H:%M:%S+00:00")
end

local function split(line)
  local out = {}
  for field in (line .. "\t"):gmatch("([^\t]*)\t") do out[#out + 1] = field end
  return out
end

local function read_lines(path)
  local fh = io.open(path, "r")
  if not fh then return nil end
  local lines = {}
  for line in fh:lines() do
    line = line:gsub("\r$", "")
    if line ~= "" and line:sub(1, 1) ~= "#" then lines[#lines + 1] = line end
  end
  fh:close()
  return lines
end

local function write_atomic(path, header, rows)
  local tmp = path .. ".tmp"
  local fh = io.open(tmp, "w")
  if not fh then return false end
  fh:write(header, "\n")
  for _, row in ipairs(rows) do fh:write(row, "\n") end
  fh:close()
  os.remove(path)
  return os.rename(tmp, path)
end

-- darktable's labels as "red,blue" ("-" for none), and its first one.
function M.labels_of(image)
  local on = {}
  for _, name in ipairs(LABELS) do
    if image[name] then on[#on + 1] = name end
  end
  return (#on > 0 and table.concat(on, ",") or "-"), on[1]
end

function M.state_of(image)
  local labels = M.labels_of(image)
  return tostring(image.rating) .. "|" .. labels
end

-- path key -> image, for everything in darktable's library.
function M.index()
  local idx = {}
  for i = 1, #dt.database do
    local img = dt.database[i]
    idx[M.key(img.path .. "\\" .. img.filename)] = img
  end
  return idx
end

local function load_snapshot()
  local snap = {}
  local lines = read_lines(join(exchange(), "darktable-snapshot.tsv"))
  if not lines then return nil end
  for _, line in ipairs(lines) do
    local f = split(line)
    if #f >= 2 then snap[f[1]] = f[2] end
  end
  return snap
end

local function save_snapshot(idx)
  local rows = {}
  for key, img in pairs(idx) do rows[#rows + 1] = key .. "\t" .. M.state_of(img) end
  table.sort(rows)
  write_atomic(join(exchange(), "darktable-snapshot.tsv"), "#darktable-snapshot 1", rows)
end

-- Lunelis -> darktable. Returns how many photos changed.
function M.apply_from_lunelis(idx)
  local lines = read_lines(join(exchange(), "lunelis-ratings.tsv"))
  if not lines then return 0 end
  local last = dt.preferences.read(MODULE, "last_applied", "string") or ""
  local newest, changed = last, 0
  for _, line in ipairs(lines) do
    local f = split(line)
    local path, stars, label, when = f[1], tonumber(f[2]), f[3], f[4] or ""
    if path and stars and when > last then
      local img = idx[M.key(path)]
      if img then
        local touched = false
        if img.rating ~= stars then
          img.rating = stars
          touched = true
        end
        -- One label here: Lunelis's change replaces it. Several (darktable
        -- allows that, Lunelis doesn't): only add Lunelis's, never remove yours.
        local on = {}
        for _, name in ipairs(LABELS) do if img[name] then on[#on + 1] = name end end
        local want = (label ~= "-" and label ~= "") and label:lower() or nil
        if want and not img[want] then
          if #on == 1 then img[on[1]] = false end
          img[want] = true
          touched = true
        elseif not want and #on == 1 then
          img[on[1]] = false
          touched = true
        end
        if touched then changed = changed + 1 end
      end
      if when > newest then newest = when end
    end
  end
  dt.preferences.write(MODULE, "last_applied", "string", newest)
  return changed
end

-- darktable -> Lunelis: what changed here since the snapshot. Returns how many.
function M.export_to_lunelis(idx)
  local snap = load_snapshot()
  if snap == nil then
    -- First run: darktable's existing ratings already reached Lunelis through
    -- darktable's own XMP sidecars, so start from here instead of re-sending all.
    save_snapshot(idx)
    return 0
  end
  local out_path = join(exchange(), "darktable-ratings.tsv")
  local pending = {}
  for _, line in ipairs(read_lines(out_path) or {}) do
    local f = split(line)
    pending[f[1]] = line                       -- not yet picked up by Lunelis: keep
  end
  local now, changed = utc_now(), 0
  for key, img in pairs(idx) do
    local state = M.state_of(img)
    if snap[key] ~= state then
      local labels = M.labels_of(img)
      pending[key] = key .. "\t" .. tostring(img.rating) .. "\t" .. labels .. "\t" .. now
      changed = changed + 1
    end
  end
  if changed > 0 then
    local rows = {}
    for _, row in pairs(pending) do rows[#rows + 1] = row end
    table.sort(rows)
    write_atomic(out_path, "#darktable-ratings 1", rows)
  end
  save_snapshot(idx)
  return changed
end

function M.sync(quiet)
  local idx = M.index()
  local sent = M.export_to_lunelis(idx)       -- first, so Lunelis's rows can't hide local edits
  local got = M.apply_from_lunelis(idx)
  if got > 0 then save_snapshot(idx) end      -- what Lunelis sent isn't a local change
  local msg = string.format("Lunelis: %d rating(s) from Lunelis, %d sent to Lunelis", got, sent)
  if M.status then M.status.label = msg end
  if not quiet or got > 0 or sent > 0 then dt.print(msg) end
  return got, sent
end

-- darktable wiring (skipped when the script is loaded by the tests).
if dt.register_event then
  dt.register_event(MODULE .. "_view", "view-changed", function() M.sync(true) end)
  dt.register_event(MODULE .. "_exit", "exit", function() M.export_to_lunelis(M.index()) end)
  dt.register_event(MODULE .. "_shortcut", "shortcut", function() M.sync(false) end, "Lunelis: sync ratings")
end
if dt.register_lib and dt.new_widget and dt.gui then
  M.status = dt.new_widget("label"){label = "Not synced yet"}
  dt.register_lib(MODULE, "Lunelis", true, false,
    {[dt.gui.views.lighttable] = {"DT_UI_CONTAINER_PANEL_RIGHT_CENTER", 100}},
    dt.new_widget("box"){
      orientation = "vertical",
      dt.new_widget("button"){label = "Sync with Lunelis", clicked_callback = function() M.sync(false) end},
      M.status,
    })
end

M.sync(true)
return M
