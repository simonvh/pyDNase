#!/usr/bin/env python

# Copyright (C) 2013 Jason Piper - j.piper@warwick.ac.uk
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
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

import argparse
import os
import math
import multiprocessing as mp
import sys

from clint.textui import progress,puts
import numpy as np

import pyDNase
from pyDNase import footprinting

__version__ = "0.1.1"

parser = argparse.ArgumentParser(
        description='Footprint the DHSs in a DNase-seq experiment using the '
                    'Wellington Algorithm.')
# Main arguments
parser.add_argument("regions", 
        help="BED file of the regions you want to footprint")
parser.add_argument("reads", 
        help="The BAM file containing the DNase-seq reads")
parser.add_argument("outputdir", 
        help="A writeable directory to write the results to")

# Optional arguments
parser.add_argument("-t", "--threads",
        help="Number of threads (default: 2)",
        type=int,
        default=2)
parser.add_argument("-b", "--bonferroni",
        action="store_true", 
        help="Performs a bonferroni correction (default: False)",
        default=False)
parser.add_argument("-sh", "--shoulder-sizes", 
        help="Range of shoulder sizes to try in format \"from,to,step\" (default: 35,36,1)",
        default="35,36,1",
        type=str)
parser.add_argument("-fp", "--footprint-sizes", 
        help="Range of footprint sizes to try in format \"from,to,step\" (default: 11,26,2)",
        default="11,26,2",
        type=str)
parser.add_argument("-d", "--one-dimension",
        action="store_true", 
        help="Use Wellington 1D instead of Wellington (default: False)",
        default=False)
parser.add_argument("-fdr","--FDR_cutoff", 
        help="Write footprints using the FDR selection method at a specific FDR (default: 0.01)",
        default=0.01,
        type=float)
parser.add_argument("-fdriter", "--FDR-iterations", 
        help="How many randomisations to use when performing FDR calculations (default: 100)",
        default=100,
        type=int)
parser.add_argument("-fdrlimit", "--FDR-limit", 
        help="Minimum p-value to be considered significant for FDR calculation (default: -20)",
        default=-20,
        type=int)
parser.add_argument("-pv","--pv_cutoffs", 
        help="Select footprints using a range of pvalue cutoffs (default: -10,-20,-30,-40,-50,-75,-100,-300,-500,-700",
        default="-10,-20,-30,-40,-50,-75,-100,-300,-500,-700",
        type=str) #map(int,"1,2,3".split(","))
parser.add_argument("-dm","--dont-merge-footprints",
        action="store_true", 
        help="Disables merging of overlapping footprints (Default: False)",
        default=False)
parser.add_argument("-o","--output_prefix", 
        help="The prefix for results files (default: <reads.regions>)",
        default="",type=str)
args = parser.parse_args()

def percentile(N, percent):
    """
    Find the percentile of a list of values.

    @parameter N - is a list of values.
    @parameter percent - a float value from 0.0 to 1.0.

    @return - the percentile of the values as a float
    """
    if not N:
        return None
    N = sorted(N)
    k = (len(N)-1) * percent
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(N[int(k)])
    d0 = N[int(f)] * (c-k)
    d1 = N[int(c)] * (k-f)
    return float(d0+d1)

#Sanity check parameters from the user

def xrange_from_string(range_string):
    try:
        range_string = map(int,range_string.split(","))
        range_string = range(range_string[0],range_string[1],range_string[2])
        assert len(range_string) > 0
        return range_string
    except:
        raise ValueError

try:
    args.shoulder_sizes = xrange_from_string(args.shoulder_sizes)
    args.footprint_sizes = xrange_from_string(args.footprint_sizes)
except ValueError:
    raise RuntimeError("shoulder and footprint sizes must be supplied as from,to,step")

try:
    args.pv_cutoffs = map(int,args.pv_cutoffs.split(","))
except:
    raise RuntimeError("p-value cutoffs must be supplied as a string of numbers separated by commas")

assert 0 < args.FDR_cutoff < 1, "FDR must be between 0 and 1"
assert args.FDR_limit < 0, "FDR limit must be less than 0"

#Checks that the directories are empty (ignores hidden files/folders)
assert len([f for f in os.listdir(args.outputdir) if f[0] != "."]) == 0, "output directory {0} is not empty!".format(args.outputdir)

if not args.output_prefix:
    args.output_prefix = str(os.path.basename(args.reads)) + "." + str(os.path.basename(args.regions))

#Load reads and regions
regions = pyDNase.GenomicIntervalSet(args.regions)
reads = pyDNase.BAMHandler(args.reads,caching=False)

#Create a directory for p-values and WIG output. This /should/ be OS independent
os.makedirs(os.path.join(args.outputdir,"p value cutoffs"))
wigout = open(os.path.relpath(args.outputdir) + "/" + args.output_prefix + ".WellingtonFootprints.wig","w")
fdrout = open(os.path.relpath(args.outputdir) + "/" + args.output_prefix + ".WellingtonFootprints.FDR.{0}.bed".format(args.FDR_cutoff),"w")

#Required for UCSC upload
print >> wigout, "track type=wiggle_0"

#Iterate in chromosome, basepair order
orderedbychr = [item for sublist in sorted(regions.intervals.values()) for item in sorted(sublist, key=lambda peak: peak.startbp)]


def footprint_regions(intervals, reads, args):
    #Calculate footprint scores (1D or 2D)
    #TODO: put args here.
    fps = []
    for each in intervals:
        #sys.stderr.write("{}\n".format(each))
        if args.one_dimension:
            fp = footprinting.wellington1D(each, reads, shoulder_sizes = args.shoulder_sizes ,footprint_sizes = args.footprint_sizes, bonferroni = args.bonferroni)
        else:
            fp = footprinting.wellington(each, reads, shoulder_sizes = args.shoulder_sizes ,footprint_sizes = args.footprint_sizes, bonferroni = args.bonferroni)
        
        #FDR footprints
        fdr = percentile(
                np.concatenate(
                    [fp.calculate(
                        reads,FDR=True, 
                        shoulder_sizes = args.shoulder_sizes,
                        footprint_sizes = args.footprint_sizes, 
                        bonferroni = args.bonferroni)[0] for i in range(
                            args.FDR_iterations)]).tolist(),args.FDR_cutoff)
        fdr_fps = []
        if fdr < args.FDR_limit:
            for footprint in fp.footprints(withCutoff=fdr,merge=not args.dont_merge_footprints):
                fdr_fps.append(footprint)

        fps.append([fp, fdr_fps])
        #sys.stderr.write("{}\n".format(fp))
    
    return fps

def map_f(a):
    return footprint_regions(a, reads, args)

pool = mp.Pool(args.threads)
puts("Calculating footprints ({} threads) ...".format(args.threads))
result = pool.map(map_f, np.array_split(np.array(orderedbychr), 32))

puts("Writing output")
for chunk in result:
    for fp, fdr_fps in chunk:
        #Write fpscores to WIG
        print >> wigout, "fixedStep\tchrom=" + str(fp.interval.chromosome) + "\t start="+ str(fp.interval.startbp) +"\tstep=1"
        for i in fp.scores:
            print >> wigout, i

        #FDR footprints
        for footprint in fdr_fps:
            print >> fdrout, footprint

        #p-value cutoff footprints
        for fpscore in args.pv_cutoffs:
            ofile = open(os.path.relpath(
                os.path.join(args.outputdir,
                    "p_value_cutoffs")) + "/" + args.output_prefix + ".WellingtonFootprints.{0}.bed".format(fpscore),"a")
            for footprint in fp.footprints(withCutoff=fpscore):
                print >> ofile, footprint
            ofile.close()
wigout.close()
