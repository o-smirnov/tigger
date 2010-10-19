#!/usr/bin/python
# -*- coding: utf-8 -*-

import sys
import pyfits
import re
import os.path
import pyfits
import math
import numpy
from math import *
from astLib.astWCS import WCS

DEG = math.pi/180;

NATIVE = "Tigger";

if __name__ == '__main__':
  import Kittens.utils
  from Kittens.utils import curry
  _verbosity = Kittens.utils.verbosity(name="convert-model");
  dprint = _verbosity.dprint;
  dprintf = _verbosity.dprintf;

  # find Tigger
  try:
    import Tigger
  except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))));
    try:
      import Tigger
    except:
      print "Unable to import the Tigger package. Please check your installation and PYTHONPATH.";
      sys.exit(1);
  from Tigger.Tools import Imaging
  from Tigger.Models import SkyModel,ModelClasses

  # setup some standard command-line option parsing
  #
  from optparse import OptionParser
  parser = OptionParser(usage="""%prog: sky_model output_image [output_model]""",
                        description="""Converts sources in a sky model into a brick (FITS image.)
Input 'sky_model' should be a Tigger-format sky model.
The 'output_image' should already exist. (Use lwimager or something similar to make a sky image.)
If an 'output_model' is specified, then sources converted into the brick will be removed from the model,
while the brick itself will be added (as a FITS image component), and a new sky model will be written out.""");
  parser.add_option("-f","--force",action="store_true",
                    help="Forces overwrite of output model.");
  parser.add_option("-s","--subset",type="string",
                    help="Selects subset of sources. Use a comma- (or space) separated list of selection tokens. A token can be "
                    "a source name, or [N]:[M] to select sources in order of brightness from N up to and not including M, or =tag to select sources "
                   "with the specified tag. Prefix with ! or - to negate a selection token.");
  parser.add_option("-F","--freq",type="float",metavar="MHz",
                    help="Sets the frequency at which an image will be generated. This affects sources with a spectral index or an RM. Default is to use "
                    "the reference frequency of the model.");
  parser.add_option("-b","--primary-beam",type="string",metavar="EXPR",
                    help="Apply a primary (power) beam expression to source fluxes. Any valid Python expression using the variables 'r' and 'fq' is accepted. "
                      "Example (for the WSRT-like 25m dish PB): \"cos(min(65*fq*1e-9*r,1.0881))**6\". NB: this particular expression can be simply specified as --primary-beam wsrt. "
                      "Also available is a slightly different --primary-beam newstar");
  parser.add_option("-p","--padding",type="float",metavar="PAD",
                    help="Sets the pad factor attribute of the resulting FITS image component. Default is %default.");
  parser.add_option("-x","--x-offset",type="float",metavar="FRACPIX",
                    help="Offsets the FITS image by this many pixels in the X direction.");
  parser.add_option("-y","--y-offset",type="float",metavar="FRACPIX",
                    help="Offsets the FITS image by this many pixels in the Y direction.");
  parser.add_option("-N","--source-name",type="string",metavar="NAME",
                    help="Name for source component corresponding to image. Default is to use the basename of the FITS file");
  parser.add_option("--add-to-image",action="store_true",
                    help="Adds sources to contents of FITS image. Default is to overwrite image data.");
  parser.add_option("--keep-sources",action="store_true",
                    help="Keeps sources in the sky model. Default is to remove sources that have been put into the brick.");
  parser.add_option("-d", "--debug",dest="verbose",type="string",action="append",metavar="Context=Level",
                    help="(for debugging Python code) sets verbosity level of the named Python context. May be used multiple times.");

  parser.set_defaults(freq=None,padding=1,x_offset=0,y_offset=0,subset="all");

  (options,rem_args) = parser.parse_args();

 # get filenames
  if len(rem_args) == 2:
    skymodel,fitsfile = rem_args;
    output_model = None;
  elif len(rem_args) == 3:
    skymodel,fitsfile,output_model = rem_args;
  else:
    parser.error("Incorrect number of arguments. Use -h for help.");

  # check if we need to overwrite
  if output_model and os.path.exists(output_model) and not options.force:
    print "Output file %s already exists. Use the -f switch to overwrite."%output_model;
    sys.exit(1);

  # load model, apply selection
  model = Tigger.load(skymodel);
  print "Loaded model",skymodel;
  # apply selection
  sources0 = model.getSourceSubset(options.subset);
  #  make sure only point sources are left
  sources = [ src for src in sources0 if src.typecode == "pnt" ];
  print "Selection leaves %d source(s), of which %d are point source(s)"%(len(sources0),len(sources));

  if not sources:
    print "There's nothing to convert into a brick.";
    sys.exit(1);

  # get PB expression
  pbfunc = None;
  if options.primary_beam:
    if options.primary_beam.upper() == "WSRT":
      pbfunc = lambda r,fq:cos(min(65*fq*1e-9*r,1.0881))**6;
      print "Primary beam expression is standard WSRT cos^6: 'cos(min(65*fq*1e-9*r,1.0881))**6'";
    elif options.primary_beam.upper() == "NEWSTAR":
      pbfunc = lambda r,fq:max(cos(65*1e-9*fq*r)**6,.01);
      print "Primary beam expression is standard NEWSTAR cos^6: 'max(cos(65*1e-9*fq*r)**6,.01)'";
    else:
      try:
        pbfunc = eval("lambda r,fq:"+options.primary_beam);
      except Exception,err:
        print "Error parsing primary beam expression %s: %s"%(options.primary_beam,str(err));
        sys.exit(1);
      print "Primary beam expression is ",options.primary_beam;

  # get frequency
  freq = (options.freq or 1)*1e+6;
  print "Brick frequency is %f MHz"%(freq*1e-6);

  # read fits file
  try:
    input_hdu = pyfits.open(fitsfile)[0];
    hdr = input_hdu.header;
  except Exception,err:
    print "Error reading FITS file %s: %s"%(fitsfile,str(err));
    sys.exit(1);
  print "Using FITS file",fitsfile;

  # reset data if asked to
  if not options.add_to_image:
    input_hdu.data[...] = 0;
    print "Contents of FITS image will be reset";
  else:
    print "Adding source(s) to FITS image";
  # Parse header to figure out RA and DEC axes
  ra_axis = dec_axis = None;
  for iaxis in range(1,hdr['NAXIS']+1):
    name = hdr.get("CTYPE%d"%iaxis,'').upper();
    if name.startswith("RA"):
      ra_axis = iaxis;
      ra0pix = hdr["CRPIX%d"%iaxis]-1;
    elif name.startswith("DEC"):
      dec_axis = iaxis;
      dec0pix = hdr["CRPIX%d"%iaxis]-1;
  if ra_axis is None or dec_axis is None:
    print "Can't find RA and/or DEC axis in this FITS image";
    sys.exit(1);

  # make WCS from header
  wcs = WCS(hdr,mode='pyfits');
  ra0,dec0 = wcs.pix2wcs(ra0pix,dec0pix);
  print "Image reference pixel (%d,%d) is at %f,%f deg"%(ra0pix,dec0pix,ra0,dec0);

  # apply x/y pixel offset
  if options.x_offset or options.y_offset:
    ra0,dec0 = wcs.pix2wcs(ra0pix+options.x_offset,dec0pix+options.y_offset);
    print "Applying x/y offset moves this to %f,%f deg"%(ra0,dec0);
    hdr["CRVAL%d"%ra_axis] = ra0;
    hdr["CRVAL%d"%dec_axis] = dec0;
    wcs = WCS(hdr,mode='pyfits');

  # insert sources
  Imaging.restoreSources(input_hdu,sources,0,primary_beam=pbfunc,freq=freq);
  # save fits file
  try:
    input_hdu.writeto(fitsfile,clobber=True);
  except Exception,err:
    print "Error writing FITS file %s: %s"%(fitsfile,str(err));
    sys.exit(1);
  print "Added %d source(s) into FITS file %s"%(len(sources),fitsfile);
  print "Using pad factor",options.padding;

  # remove sources from model if asked to
  if not options.keep_sources:
    selected = set([src.name for src in sources]);
    sources = [ src for src in model.sources if not src.name in selected ];
  else:
    sources = model.sources;

  # add image to model
  if output_model:
    # get image parameters
    max_flux = float(input_hdu.data.max());
    ra0 *= DEG;
    dec0 *= DEG;
    sx,sy = wcs.getHalfSizeDeg();
    sx *= DEG;
    sy *= DEG;
    nx,ny = input_hdu.data.shape[-1:-3:-1];
    # check if this image is already contained in the model
    for src in model.sources:
      if isinstance(getattr(src,'shape',None),ModelClasses.FITSImage) and os.path.samefile(src.shape.filename,fitsfile):
        print "Model already contains a component (%s) for this image. Updating the component"%src.name;
        # update source parameters
        src.position.ra,src.position.dec = ra0,dec0;
        src.flux.I = max_flux;
        src.shape.ex,src.shape.ey = sx,sy;
        src.shape.nx,src.shape.ny = nx,ny;
        src.shape.pad = pad;
        break;
    # not contained, make new source object
    else:
      pos = ModelClasses.Position(ra0,dec0);
      flux = ModelClasses.Flux(max_flux);
      shape = ModelClasses.FITSImage(sx,sy,0,fitsfile,nx,ny,pad=options.padding);
      sname = options.source_name or os.path.splitext(os.path.basename(fitsfile))[0];
      img_src = SkyModel.Source(sname,pos,flux,shape=shape);
      print "Inserting new model component named %s"%sname;
      sources.append(img_src);
    # save model
    model.setSources(sources);
    model.save(output_model);
    print "Saved %d source(s) to output model %s."%(len(model.sources),output_model);
