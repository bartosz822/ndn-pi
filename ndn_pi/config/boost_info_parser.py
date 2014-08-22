
# -*- Mode:python; c-file-style:"gnu"; indent-tabs-mode:nil -*- */
#
# Copyright (C) 2014 Regents of the University of California.
# Author: Adeola Bannis 
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
# A copy of the GNU General Public License is in the file COPYING.


import shlex
from collections import OrderedDict

"""
This class is provided for compatibility with the Boost INFO property list
format used in ndn-cxx.
"""

class BoostInfoTree(object):
    def __init__(self, value = None, parent = None):
        super(BoostInfoTree, self).__init__()
        self.subtrees = OrderedDict()
        self.value = value
        self.parent = parent

        self.lastChild = None

    def clone(self):

        copy = BoostInfoTree(self.value)
        for subtreeName, subtrees in self.subtrees.items():
            for t in subtrees:
                newTree = t.clone()
                copy.addSubtree(subtreeName, newTree)
        return copy

    def addSubtree(self, treeName, newTree):
        if treeName in self.subtrees:
            self.subtrees[treeName].append(newTree)
        else:
            self.subtrees[treeName] = [newTree]
        newTree.parent = self
        self.lastChild = newTree

    def createSubtree(self, treeName, value=None ):
        newTree = BoostInfoTree(value, self)
        self.addSubtree(treeName, newTree)
        return newTree

    def __getitem__(self, key):
        key = key.lstrip('/')
        path = key.split('/')
        if len(key) == 0:
            return [self]

        subtrees = self.subtrees[path[0]]
        if len(path) == 1:
            return subtrees

        newPath = '/'.join(path[1:])
        foundVals = []
        for t in subtrees:
            foundVals.extend(t.__getitem__(newPath))
        return foundVals

    def getValue(self):
        return self.value

    def _prettyprint(self, indentLevel=1):
        prefix = " "*indentLevel
        s = ""
        if self.parent is not None:
            if self.value is not None and len(self.value) > 0:
                s += "\"" + str(self.value) + "\""
            s+= "\n" 
        if len(self.subtrees) > 0:
            if self.parent is not None:
                s += prefix+ "{\n"
            nextLevel = " "*(indentLevel+2)
            for t in self.subtrees:
                for subtree in self.subtrees[t]:
                    s += nextLevel + str(t) + " " + subtree._prettyprint(indentLevel+2)
            if self.parent is not None:
                s +=  prefix + "}\n"
        return s

    def __str__(self):
        return self._prettyprint()


class BoostInfoParser(object):
    def __init__(self):
        self._reset()

    def _reset(self):
        self._root = BoostInfoTree()
        self._root.lastChild = self

    def read(self, filename):
        self._read(filename, self._root)
        return self._root

    def readPropertyList(self, fromDict):
        if not isinstance(fromDict, dict):
            raise TypeError('BoostInfoTree must be initialized from dictionary')
        self._readDict(fromDict, self._root)
        return self._root

    def _read(self, filename, ctx):
        with open(filename, 'r') as stream:
            for line in stream:
                ctx = self._parseLine(line.strip(), ctx)
        return ctx

    def _readList(self, fromList, intoNode, keyName):
        # we can have lists of strings or dicts, ONLY
        for v in fromList:
            if hasattr(v, 'keys'):
                newNode = intoNode.createSubtree(k)
                self._readDict(v, newNode)
            else:
                intoNode.createSubtree(keyName, v)

    def _readDict(self, fromDict, currentNode):
        for k,v in fromDict.items():
            # HACK
            if k == '__name__':
                continue
            if hasattr(v, 'keys'):
                newNode = currentNode.createSubtree(k)
                self._readDict(v, newNode)
            elif hasattr(v, '__iter__'):
                self._readList(v, currentNode, k)
            else:
                # should be a string, should I check?
                currentNode.createSubtree(k,v)


    def write(self, filename):
        with open(filename, 'w') as stream:
            stream.write(str(self._root))

    def _parseLine(self, string, context):
        # skip blank lines and comments
        commentStart = string.find(";")
        if commentStart >= 0:
           string = string[:commentStart].strip()
        if len(string) == 0:
           return context

        # usually we are expecting key and optional value
        strings = shlex.split(string)
        isSectionStart = False
        isSectionEnd=False
        for s in strings:
            isSectionStart = isSectionStart or s == '{'
            isSectionEnd = isSectionEnd or s == '}'

        if not isSectionStart and not isSectionEnd:
            key = strings[0]
            if len(strings) > 1:
                val = strings[1]
            else:
                val = None
            #if it is an "#include", load the new file instead of inserting keys
            if key == "#include":
                context = self._read(val, context)
            else:
                newTree = context.createSubtree(key, val)

            return context
        # ok, who is the joker who put a { on the same line as the key name?!
        sectionStart = string.find('{')
        if sectionStart > 0:
            firstPart = string[:sectionStart]
            secondPart = string[sectionStart:]

            ctx = self._parseLine(firstPart, context)
            return self._parseLine(secondPart, ctx)


        #if we encounter a {, we are beginning a new context
        # TODO: error if there was already a subcontext here
        if string[0] == '{':
            context = context.lastChild 
            return context

        # if we encounter a }, we are ending a list context
        if string[0] == '}':
            context = context.parent
            return context


    def getRoot(self):
        return self._root

    def __getitem__(self, key):
        return self._root.__getitem__(key)
